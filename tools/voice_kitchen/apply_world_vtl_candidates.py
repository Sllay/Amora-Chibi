#!/usr/bin/env python3
"""Apply conservative WORLD source-filter transforms to the preferred Amora mix.

This lab intentionally separates three dimensions that simple pitch shifting or
uniform formant shifting conflates:

- F0 (source pitch contour)
- spectral envelope (vocal-tract / resonance shape)
- aperiodicity (noisy / breathy component)

The goal is NOT to create the final Vocaloid texture yet. It is to find a
cleaner young/feminine base without chipmunk or alien artefacts.
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
    pitch_semitones: float
    tract_ratio: float
    aperiodicity_scale: float


DEFAULT_CANDIDATES = (
    WorldCandidate("WORLD_RESYNTH_NEUTRAL", 0.0, 1.0, 1.0),
    WorldCandidate("WORLD_VTL_1P025", 0.0, 1.025, 1.0),
    WorldCandidate("WORLD_VTL_1P04_CLEAN", 0.20, 1.04, 0.98),
    WorldCandidate("WORLD_VTL_1P055_CLEAN", 0.35, 1.055, 0.96),
)


def semitone_ratio(semitones: float) -> float:
    return 2.0 ** (semitones / 12.0)


def tract_ratio_to_semitones(ratio: float) -> float:
    return 12.0 * math.log2(ratio)


def _warp_spectral_envelope(sp, sample_rate: int, tract_ratio: float):
    import numpy as np

    if abs(tract_ratio - 1.0) < 1e-9:
        return sp.copy()

    bins = sp.shape[1]
    freqs = np.linspace(0.0, sample_rate / 2.0, bins, dtype=np.float64)
    source_freqs = np.clip(freqs / tract_ratio, 0.0, sample_rate / 2.0)
    warped = np.empty_like(sp)

    for frame_index, frame in enumerate(sp):
        out = np.interp(source_freqs, freqs, frame)
        # Preserve broad frame energy so the audition is about resonance/timbre
        # rather than a loudness jump introduced by the frequency warp.
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

    f0_out = f0.copy()
    voiced = f0_out > 0.0
    f0_out[voiced] *= semitone_ratio(candidate.pitch_semitones)

    sp_out = _warp_spectral_envelope(sp, sample_rate, candidate.tract_ratio)
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
    return y


def main() -> int:
    parser = argparse.ArgumentParser(description="WORLD VTL/F0/ap audition for Amora Voice Kitchen")
    parser.add_argument("--root", type=Path, default=Path("voice-kitchen-wavs"))
    args = parser.parse_args()

    import numpy as np
    import soundfile as sf

    report = {
        "source_name": "amora_mix_p60_s40.wav",
        "method": "WORLD DIO+StoneMask / CheapTrick / D4C / synthesize",
        "outputs": 0,
        "note": (
            "RTF medido no runner GitHub x86_64. Não certifica Galaxy A15. "
            "WORLD_RESYNTH_NEUTRAL serve para separar artefato do vocoder de artefato do transform."
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
            y = transform_world(x, int(sample_rate), candidate)
            elapsed = time.perf_counter() - started
            rtf = elapsed / duration if duration > 0 else 0.0
            all_rtfs.append(rtf)

            output_name = candidate.id.lower() + ".wav"
            output_path = phrase_dir / output_name
            sf.write(str(output_path), y, int(sample_rate), subtype="PCM_16")

            phrase_entry["candidates"].append(
                {
                    "id": candidate.id,
                    "pitch_semitones": candidate.pitch_semitones,
                    "tract_ratio": candidate.tract_ratio,
                    "tract_shift_equivalent_semitones": round(
                        tract_ratio_to_semitones(candidate.tract_ratio), 4
                    ),
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
