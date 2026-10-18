#!/usr/bin/env python3
"""Render the final A/B between CLEAR_POLISHED and PLUS_POLISHED.

BK's listening feedback established a narrow trade-off:
- CLEAR_POLISHED is easier to understand / cleaner;
- PLUS_POLISHED sounds a little more natural.

Rather than opening another broad search, this script creates only two bounded
intermediate candidates and keeps the successful WORLD child-VTL pipeline
unchanged. FINAL_A leans 60% toward CLEAR; FINAL_B leans 60% toward PLUS.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
import time
from pathlib import Path


BASE_MODULE_PATH = Path(__file__).with_name("apply_world_vtl_candidates.py")
SPEC = importlib.util.spec_from_file_location("amora_voice_kitchen_world_base", BASE_MODULE_PATH)
assert SPEC is not None and SPEC.loader is not None
BASE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = BASE
SPEC.loader.exec_module(BASE)

WorldCandidate = BASE.WorldCandidate
transform_world = BASE.transform_world
ratio_to_semitones = BASE.ratio_to_semitones


FINAL_CANDIDATES = (
    WorldCandidate(
        "AMORA_CHILD_FINAL_A_CLEAR60",
        283.2,
        1.184,
        1.047,
        (
            (0.0, 1.0),
            (250.0, 1.0),
            (700.0, 1.124),
            (1500.0, 1.109),
            (3000.0, 1.067),
            (5000.0, 1.020),
            (8000.0, 1.000),
        ),
        1.0,
        -2.24,
    ),
    WorldCandidate(
        "AMORA_CHILD_FINAL_B_PLUS60",
        284.8,
        1.186,
        1.048,
        (
            (0.0, 1.0),
            (250.0, 1.0),
            (700.0, 1.126),
            (1500.0, 1.111),
            (3000.0, 1.068),
            (5000.0, 1.020),
            (8000.0, 1.000),
        ),
        1.0,
        -2.36,
    ),
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Final CLEAR/PLUS A/B for Amora Voice Kitchen")
    parser.add_argument("--root", type=Path, default=Path("voice-kitchen-wavs"))
    args = parser.parse_args()

    import numpy as np
    import soundfile as sf

    report = {
        "source_name": "amora_mix_p60_s40.wav",
        "method": "Final interpolation inside the approved WORLD child-VTL region",
        "candidate_policy": {
            "A": "60% CLEAR_POLISHED / 40% PLUS_POLISHED",
            "B": "40% CLEAR_POLISHED / 60% PLUS_POLISHED",
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

        phrase_entry = {"phrase": phrase_dir.name, "candidates": []}
        for candidate in FINAL_CANDIDATES:
            started = time.perf_counter()
            y, source_f0_median, applied_f0_ratio = transform_world(x, int(sample_rate), candidate)
            elapsed = time.perf_counter() - started

            output_path = phrase_dir / (candidate.id.lower() + ".wav")
            sf.write(str(output_path), y, int(sample_rate), subtype="PCM_16")

            phrase_entry["candidates"].append(
                {
                    "id": candidate.id,
                    "target_f0_median_hz": candidate.target_f0_median_hz,
                    "source_f0_median_hz": round(source_f0_median, 3) if source_f0_median is not None else None,
                    "applied_f0_ratio": round(applied_f0_ratio, 6),
                    "applied_f0_shift_semitones": round(ratio_to_semitones(applied_f0_ratio), 4),
                    "pitch_range_scale": candidate.pitch_range_scale,
                    "spectral_ratios": [list(item) for item in candidate.spectral_ratios],
                    "high_shelf_db": candidate.high_shelf_db,
                    "output": str(output_path.relative_to(args.root)),
                    "processing_seconds": round(elapsed, 6),
                    "processing_rtf": round(elapsed / duration, 6) if duration > 0 else 0.0,
                }
            )
            report["outputs"] += 1
            print(output_path)

        report["phrases"].append(phrase_entry)

    (args.root / "final-ab-report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
