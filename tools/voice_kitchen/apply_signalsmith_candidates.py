#!/usr/bin/env python3
"""Apply bounded Signalsmith Stretch pitch/formant candidates to Voice Kitchen WAVs.

This stage intentionally starts from the preferred Supertonic 60/40 F1/F5 mix
and changes vocal-tract/formant character independently from pitch.  The goal
is to stop using playback-rate pitch as a proxy for femininity/youth and to
measure a production-shaped DSP candidate before touching Android.

The script shells out to the *official* Signalsmith Stretch command-line
example compiled from a pinned upstream commit by CI.  It also records rough
processing RTF on the CI host.  That RTF is comparative evidence only; it is
not Galaxy A15 certification.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable


@dataclass(frozen=True)
class SignalsmithCandidate:
    id: str
    pitch_semitones: float
    formant_semitones: float
    formant_base_hz: float = 0.0
    block_ms: float = 120.0
    overlap: float = 4.0


# Keep the first pass conservative.  Speaker identity should remain recognisable,
# while the listener can compare formant-only changes against a tiny F0 nudge.
DEFAULT_CANDIDATES: tuple[SignalsmithCandidate, ...] = (
    SignalsmithCandidate(
        id="SS_FORMANT_1P5_PITCH_0",
        pitch_semitones=0.0,
        formant_semitones=1.5,
    ),
    SignalsmithCandidate(
        id="SS_FORMANT_2P5_PITCH_0",
        pitch_semitones=0.0,
        formant_semitones=2.5,
    ),
    SignalsmithCandidate(
        id="SS_FORMANT_2P5_PITCH_0P5",
        pitch_semitones=0.5,
        formant_semitones=2.5,
    ),
    SignalsmithCandidate(
        id="SS_FORMANT_3P5_PITCH_0P5",
        pitch_semitones=0.5,
        formant_semitones=3.5,
    ),
)


def signalsmith_command(
    stretch_bin: Path,
    source: Path,
    output: Path,
    candidate: SignalsmithCandidate,
) -> list[str]:
    """Build the pinned official CLI invocation for one deterministic candidate."""

    return [
        str(stretch_bin),
        f"--semitones={candidate.pitch_semitones:g}",
        f"--formant={candidate.formant_semitones:g}",
        f"--formant-base={candidate.formant_base_hz:g}",
        f"--block-ms={candidate.block_ms:g}",
        f"--overlap={candidate.overlap:g}",
        str(source),
        str(output),
    ]


def wav_duration_seconds(path: Path) -> float:
    with wave.open(str(path), "rb") as wav:
        sample_rate = wav.getframerate()
        frames = wav.getnframes()
    if sample_rate <= 0:
        raise ValueError(f"sample rate inválido em {path}: {sample_rate}")
    return frames / sample_rate


def iter_sources(root: Path, source_name: str) -> Iterable[tuple[Path, Path]]:
    for phrase_dir in sorted(root.glob("phrase_*")):
        if not phrase_dir.is_dir():
            continue
        source = phrase_dir / source_name
        if source.is_file():
            yield phrase_dir, source


def render_candidates(
    *,
    stretch_bin: Path,
    root: Path,
    source_name: str,
    candidates: tuple[SignalsmithCandidate, ...] = DEFAULT_CANDIDATES,
) -> dict[str, object]:
    if not stretch_bin.is_file():
        raise FileNotFoundError(f"Signalsmith CLI não encontrado: {stretch_bin}")

    phrase_reports: list[dict[str, object]] = []
    total_outputs = 0

    for phrase_dir, source in iter_sources(root, source_name):
        source_duration = wav_duration_seconds(source)
        candidate_reports: list[dict[str, object]] = []

        for candidate in candidates:
            output = phrase_dir / f"{candidate.id.lower()}.wav"
            command = signalsmith_command(stretch_bin, source, output, candidate)
            started = time.perf_counter()
            subprocess.run(command, check=True)
            elapsed = time.perf_counter() - started
            total_outputs += 1

            candidate_reports.append(
                {
                    **asdict(candidate),
                    "output": str(output.relative_to(root)),
                    "source_duration_seconds": round(source_duration, 6),
                    "processing_seconds": round(elapsed, 6),
                    "processing_rtf": round(elapsed / source_duration, 6)
                    if source_duration > 0
                    else None,
                }
            )

        phrase_reports.append(
            {
                "phrase": phrase_dir.name,
                "source": str(source.relative_to(root)),
                "candidates": candidate_reports,
            }
        )

    if not phrase_reports:
        raise FileNotFoundError(
            f"nenhum {source_name} encontrado em diretórios phrase_* dentro de {root}"
        )

    rtfs = [
        candidate["processing_rtf"]
        for phrase in phrase_reports
        for candidate in phrase["candidates"]
        if candidate["processing_rtf"] is not None
    ]
    return {
        "source_name": source_name,
        "outputs": total_outputs,
        "ci_processing_rtf_mean": round(sum(rtfs) / len(rtfs), 6) if rtfs else None,
        "ci_processing_rtf_max": round(max(rtfs), 6) if rtfs else None,
        "note": (
            "RTF medido no runner GitHub x86_64; serve para comparação e não "
            "certifica latência/CPU no Galaxy A15."
        ),
        "phrases": phrase_reports,
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "Aplica candidatos Signalsmith de formant/pitch ao mix Supertonic 60/40 "
            "e registra RTF comparativo."
        )
    )
    parser.add_argument("--stretch-bin", required=True, type=Path)
    parser.add_argument("--root", type=Path, default=Path("voice-kitchen-wavs"))
    parser.add_argument(
        "--source-name",
        default="amora_mix_p60_s40.wav",
        help="WAV base dentro de cada phrase_*.",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="JSON de saída; padrão: <root>/signalsmith-processing-report.json",
    )
    args = parser.parse_args()

    report = render_candidates(
        stretch_bin=args.stretch_bin,
        root=args.root,
        source_name=args.source_name,
    )
    report_path = args.report or args.root / "signalsmith-processing-report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
