#!/usr/bin/env python3
"""Render Amora's first subtle VoiceSignature on top of the frozen FINAL_B base.

FINAL_B already solved the age/size problem and was explicitly selected by BK.
This pass must NOT reopen that search. It therefore copies FINAL_B's F0 target,
VTL warp and HF shelf exactly, then adds only two very small texture cues:

1. slightly more periodic WORLD excitation (aperiodicity 0.965x), which nudges
   the result toward a clean vocal-synth texture without turning it robotic;
2. a 5% RMS-matched soft harmonic layer, adding a tiny deterministic colour
   without bitcrush, sample-hold, ring modulation, or any extra pitch shift.

This is an audition candidate for VoiceSignature identity. Production/Android
promotion still requires BK listening and the normal physical-device gate.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path


FINAL_MODULE_PATH = Path(__file__).with_name("apply_world_final_ab.py")
SPEC = importlib.util.spec_from_file_location("amora_voice_kitchen_final", FINAL_MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
FINAL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FINAL
SPEC.loader.exec_module(FINAL)

BASE = FINAL.BASE
WorldCandidate = FINAL.WorldCandidate
transform_world = FINAL.transform_world
FINAL_B = FINAL.FINAL_CANDIDATES[1]

SIGNATURE_ID = "AMORA_VOICE_SIGNATURE_V1_SOFT"
APERIODICITY_SCALE = 0.965
HARMONIC_DRIVE = 1.30
HARMONIC_WET = 0.05

SIGNATURE_CANDIDATE = WorldCandidate(
    SIGNATURE_ID,
    FINAL_B.target_f0_median_hz,
    FINAL_B.max_f0_ratio,
    FINAL_B.pitch_range_scale,
    FINAL_B.spectral_ratios,
    APERIODICITY_SCALE,
    FINAL_B.high_shelf_db,
)


def apply_soft_harmonic_texture(samples, *, drive: float = HARMONIC_DRIVE, wet: float = HARMONIC_WET):
    """Blend a tiny soft-saturation layer while preserving RMS and peak safety."""
    import numpy as np

    x = np.asarray(samples, dtype=np.float64)
    if x.size == 0 or wet <= 0.0:
        return x.copy()

    wet = float(max(0.0, min(0.12, wet)))
    drive = float(max(1.0, min(2.0, drive)))
    norm = float(np.tanh(drive))
    shaped = np.tanh(x * drive) / max(norm, 1e-9)
    y = x * (1.0 - wet) + shaped * wet

    # Keep the signature from winning merely by sounding louder.
    source_rms = float(np.sqrt(np.mean(x * x)))
    output_rms = float(np.sqrt(np.mean(y * y)))
    if source_rms > 1e-9 and output_rms > 1e-9:
        y *= source_rms / output_rms

    peak = float(np.max(np.abs(y)))
    if peak > 0.99:
        y *= 0.99 / peak
    return y


def main() -> int:
    parser = argparse.ArgumentParser(description="Subtle VoiceSignature audition for frozen FINAL_B")
    parser.add_argument("--root", type=Path, default=Path("voice-kitchen-wavs"))
    args = parser.parse_args()

    import numpy as np
    import soundfile as sf

    report = {
        "base": "AMORA_CHILD_FINAL_B_PLUS60",
        "signature": SIGNATURE_ID,
        "policy": "preserve FINAL_B identity; add only subtle non-robotic texture",
        "parameters": {
            "target_f0_median_hz": SIGNATURE_CANDIDATE.target_f0_median_hz,
            "max_f0_ratio": SIGNATURE_CANDIDATE.max_f0_ratio,
            "pitch_range_scale": SIGNATURE_CANDIDATE.pitch_range_scale,
            "spectral_ratios": [list(item) for item in SIGNATURE_CANDIDATE.spectral_ratios],
            "high_shelf_db": SIGNATURE_CANDIDATE.high_shelf_db,
            "aperiodicity_scale": APERIODICITY_SCALE,
            "harmonic_drive": HARMONIC_DRIVE,
            "harmonic_wet": HARMONIC_WET,
            "bitcrush": False,
            "sample_hold": False,
            "ring_modulation": False,
            "extra_pitch_shift": False,
        },
        "outputs": 0,
        "phrases": [],
    }

    for phrase_dir in sorted(p for p in args.root.glob("phrase_*") if p.is_dir()):
        source_path = phrase_dir / "amora_mix_p60_s40.wav"
        if not source_path.is_file():
            continue

        x, sample_rate = sf.read(str(source_path), dtype="float64", always_2d=False)
        if getattr(x, "ndim", 1) == 2:
            x = np.mean(x, axis=1)
        x = np.asarray(x, dtype=np.float64)
        duration = len(x) / float(sample_rate)

        started = time.perf_counter()
        y, source_f0_median, applied_f0_ratio = transform_world(
            x,
            int(sample_rate),
            SIGNATURE_CANDIDATE,
        )
        y = apply_soft_harmonic_texture(y)
        elapsed = time.perf_counter() - started

        output_path = phrase_dir / "amora_voice_signature_v1_soft.wav"
        sf.write(str(output_path), y, int(sample_rate), subtype="PCM_16")

        report["phrases"].append(
            {
                "phrase": phrase_dir.name,
                "source_f0_median_hz": round(source_f0_median, 3) if source_f0_median is not None else None,
                "applied_f0_ratio": round(applied_f0_ratio, 6),
                "output": str(output_path.relative_to(args.root)),
                "processing_seconds": round(elapsed, 6),
                "processing_rtf": round(elapsed / duration, 6) if duration > 0 else 0.0,
            }
        )
        report["outputs"] += 1
        print(output_path)

    (args.root / "amora-signature-v1-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
