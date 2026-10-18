#!/usr/bin/env python3
"""Apply child/young-vocal-tract WORLD transforms to the preferred Amora mix.

This audition is intentionally different from the earlier uniform formant warp.
BK reported that the previous pass removed the chipmunk but still sounded like a
thin/nasal adult voice. Here we model age cues more directly:

- F0 median is moved toward a bounded target while preserving the contour;
- the spectral envelope is warped non-uniformly (stronger in low/mid formants,
  tapering toward the high spectrum instead of shifting everything equally);
- the low body below ~250 Hz is preserved;
- aperiodicity is left intact so this pass does not accidentally add a synthetic
  or over-cleaned timbre.

This is still a Voice Kitchen experiment, not the canonical VoiceSignature.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import NamedTuple


class WorldCandidate(NamedTuple):
    id: str
    target_f0_median_hz: float | None
    max_f0_ratio: float
    pitch_range_scale: float
    spectral_ratios: tuple[tuple[float, float], ...]
    aperiodicity_scale: float = 1.0


NEUTRAL_SPECTRAL_RATIOS = (
    (0.0, 1.0),
    (250.0, 1.0),
    (700.0, 1.0),
    (1500.0, 1.0),
    (3000.0, 1.0),
    (5000.0, 1.0),
    (8000.0, 1.0),
)

# The key difference from the rejected uniform formant pass is that the
# low/mid vocal-tract region moves more than the high spectrum. This changes
# perceived vocal-tract size instead of merely making the whole voice thin.
DEFAULT_CANDIDATES = (
    WorldCandidate(
        "WORLD_RESYNTH_NEUTRAL",
        None,
        1.0,
        1.0,
        NEUTRAL_SPECTRAL_RATIOS,
    ),
    WorldCandidate(
        "WORLD_CHILD_SOFT",
        245.0,
        1.10,
        1.02,
        (
            (0.0, 1.0),
            (250.0, 1.0),
            (700.0, 1.06),
            (1500.0, 1.055),
            (3000.0, 1.04),
            (5000.0, 1.02),
            (8000.0, 1.005),
        ),
    ),
    WorldCandidate(
        "WORLD_CHILD_BALANCED",
        260.0,
        1.14,
        1.04,
        (
            (0.0, 1.0),
            (250.0, 1.0),
            (700.0, 1.09),
            (1500.0, 1.08),
            (3000.0, 1.055),
            (5000.0, 1.03),
            (8000.0, 1.01),
        ),
    ),
    WorldCandidate(
        "WORLD_CHILD_CLEAR",
        275.0,
        1.18,
        1.06,
        (
            (0.0, 1.0),
            (250.0, 1.0),
            (700.0, 1.12),
            (1500.0, 1.10),
            (3000.0, 1.075),
            (5000.0, 1.04),
            (8000.0, 1.015),
        ),
    ),
)


def ratio_to_semitones(ratio: float) -> float:
    return 12.0 * math.log2(ratio)


def _target_f0(f0, candidate: WorldCandidate):
    import numpy as np

    out = f0.copy()
    voiced = out > 0.0
    if not np.any(voiced) or candidate.target_f0_median_hz is None:
        return out, None, 1.0

    source_median = float(np.median(out[voiced]))
    if source_median <= 0.0:
        return out, None, 1.0

    desired_ratio = candidate.target_f0_median_hz / source_median
    applied_ratio = max(1.0, min(candidate.max_f0_ratio, desired_ratio))
    target_median = source_median * applied_ratio

    # Work in log-F0 so we move the centre without flattening the speaker's
    # original melodic contour. A tiny range expansion is included because
    # child speech tends to have a somewhat wider relative F0 excursion.
    log_values = np.log(out[voiced])
    log_median = float(np.median(log_values))
    target_log_median = math.log(target_median)
    out[voiced] = np.exp(
        target_log_median
        + (log_values - log_median) * candidate.pitch_range_scale
    )
    return out, source_median, applied_ratio


def _piecewise_source_frequency_map(sample_rate: int, bins: int, spectral_ratios):
    import numpy as np

    nyquist = sample_rate / 2.0
    anchors = [(float(hz), float(ratio)) for hz, ratio in spectral_ratios if hz < nyquist]
    if not anchors or anchors[0][0] != 0.0:
        anchors.insert(0, (0.0, 1.0))
    if anchors[-1][0] < nyquist:
        anchors.append((nyquist, 1.0))

    source_anchor = np.asarray([hz for hz, _ in anchors], dtype=np.float64)
    ratio_anchor = np.asarray([ratio for _, ratio in anchors], dtype=np.float64)
    target_anchor = source_anchor * ratio_anchor

    # The last point must terminate exactly at Nyquist. This also prevents a
    # high-frequency stretch from producing an out-of-band mapping.
    source_anchor[-1] = nyquist
    target_anchor[-1] = nyquist

    if np.any(np.diff(target_anchor) <= 0.0):
        raise ValueError("spectral warp anchors must remain strictly monotonic")

    output_freqs = np.linspace(0.0, nyquist, bins, dtype=np.float64)
    return np.interp(output_freqs, target_anchor, source_anchor)


def _warp_spectral_envelope_piecewise(sp, sample_rate: int, spectral_ratios):
    import numpy as np

    if all(abs(float(ratio) - 1.0) < 1e-9 for _, ratio in spectral_ratios):
        return sp.copy()

    bins = sp.shape[1]
    freqs = np.linspace(0.0, sample_rate / 2.0, bins, dtype=np.float64)
    source_freqs = _piecewise_source_frequency_map(sample_rate, bins, spectral_ratios)
    warped = np.empty_like(sp)

    for frame_index, frame in enumerate(sp):
        out = np.interp(source_freqs, freqs, frame)
        source_energy = float(np.sum(frame))
        output_energy = float(np.sum(out))
        if source_energy > 0.0 and output_energy > 0.0:
            out *= source_energy / output_energy
        warped[frame_index] = out

    return warped


def transform_world(x, sample_rate: int, candidate: WorldCandidate):
    import numpy as np
    import pyworld as pw

    x = np.asarray(x, dtype=np.float64)
    frame_period_ms = 5.0

    raw_f0, temporal_positions = pw.dio(x, sample_rate, frame_period=frame_period_ms)
    f0 = pw.stonemask(x, raw_f0, temporal_positions, sample_rate)
    sp = pw.cheaptrick(x, f0, temporal_positions, sample_rate)
    ap = pw.d4c(x, f0, temporal_positions, sample_rate)

    f0_out, source_f0_median, applied_f0_ratio = _target_f0(f0, candidate)
    sp_out = _warp_spectral_envelope_piecewise(
        sp,
        sample_rate,
        candidate.spectral_ratios,
    )
    ap_out = np.clip(ap * candidate.aperiodicity_scale, 0.0, 1.0)

    y = pw.synthesize(
        f0_out.astype(np.float64),
        sp_out.astype(np.float64),
        ap_out.astype(np.float64),
        sample_rate,
        frame_period=frame_period_ms,
    )

    peak = float(np.max(np.abs(y))) if y.size else 0.0
    if peak > 0.99:
        y = y * (0.99 / peak)
    return y, source_f0_median, applied_f0_ratio


def main() -> int:
    parser = argparse.ArgumentParser(description="WORLD piecewise child-VTL audition for Amora Voice Kitchen")
    parser.add_argument("--root", type=Path, default=Path("voice-kitchen-wavs"))
    args = parser.parse_args()

    import numpy as np
    import soundfile as sf

    report = {
        "source_name": "amora_mix_p60_s40.wav",
        "method": "WORLD + bounded median-F0 + non-uniform spectral warp",
        "outputs": 0,
        "note": (
            "Age-cue audition after BK reported thin/nasal adult timbre from uniform formant shaping. "
            "Low body is preserved, low/mid formants move more than highs, and F0 contour is retained."
        ),
        "phrases": [],
    }
    all_rtfs: list[float] = []

    for phrase_dir in sorted(p for p in args.root.glob("phrase_*") if p.is_dir()):
        source_path = phrase_dir / "amora_mix_p60_s40.wav"
        if not source_path.is_file():
            continue

        x, sample_rate = sf.read(str(source_path), dtype="float64", always_2d=False)
        if getattr(x, "ndim", 1) == 2:
            x = np.mean(x, axis=1)
        x = np.asarray(x, dtype=np.float64)
        duration = len(x) / float(sample_rate)

        phrase_entry = {
            "phrase": phrase_dir.name,
            "source": str(source_path.relative_to(args.root)),
            "sample_rate_hz": int(sample_rate),
            "source_duration_seconds": round(duration, 6),
            "candidates": [],
        }

        for candidate in DEFAULT_CANDIDATES:
            started = time.perf_counter()
            y, source_f0_median, applied_f0_ratio = transform_world(
                x,
                int(sample_rate),
                candidate,
            )
            elapsed = time.perf_counter() - started
            rtf = elapsed / duration if duration > 0 else 0.0
            all_rtfs.append(rtf)

            output_name = candidate.id.lower() + ".wav"
            output_path = phrase_dir / output_name
            sf.write(str(output_path), y, int(sample_rate), subtype="PCM_16")

            phrase_entry["candidates"].append(
                {
                    "id": candidate.id,
                    "target_f0_median_hz": candidate.target_f0_median_hz,
                    "source_f0_median_hz": (
                        round(source_f0_median, 3) if source_f0_median is not None else None
                    ),
                    "applied_f0_ratio": round(applied_f0_ratio, 6),
                    "applied_f0_shift_semitones": round(ratio_to_semitones(applied_f0_ratio), 4),
                    "pitch_range_scale": candidate.pitch_range_scale,
                    "spectral_ratios": [list(item) for item in candidate.spectral_ratios],
                    "aperiodicity_scale": candidate.aperiodicity_scale,
                    "output": str(output_path.relative_to(args.root)),
                    "processing_seconds": round(elapsed, 6),
                    "processing_rtf": round(rtf, 6),
                }
            )
            report["outputs"] += 1
            print(output_path)

        report["phrases"].append(phrase_entry)

    report["ci_processing_rtf_mean"] = round(sum(all_rtfs) / len(all_rtfs), 6) if all_rtfs else None
    report["ci_processing_rtf_max"] = round(max(all_rtfs), 6) if all_rtfs else None

    (args.root / "world-processing-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
