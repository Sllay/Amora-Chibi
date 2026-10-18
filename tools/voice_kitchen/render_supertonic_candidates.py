#!/usr/bin/env python3
"""Render Amora Voice Kitchen Supertonic blends to WAV files.

This is intentionally an offline audition helper: it initializes the official
Supertonic Python runtime, reads the bundled F1/F5 style JSONs, creates the
70/30 and 60/40 timbre mixes with ``mix_supertonic_styles.py`` and renders the
same Portuguese phrases for A/B listening.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from mix_supertonic_styles import generate_candidates

PHRASES = (
    "Bom dia. Como você dormiu?",
    "Eu realmente não concordo com isso.",
    "Você deixou suas coisas espalhadas pela sala.",
    "Três pratos de trigo para três tigres tristes.",
    "Essas escolhas precisam ser consideradas com cuidado.",
    "Amor, você sabe que isso não faz o menor sentido, né?",
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Renderiza candidatos F1/F5 da Amora Voice Kitchen.")
    parser.add_argument("--out-dir", type=Path, default=Path("voice-kitchen-wavs"))
    parser.add_argument("--steps", type=int, default=8)
    parser.add_argument("--speed", type=float, default=1.0)
    args = parser.parse_args()

    from supertonic import TTS

    out_dir = args.out_dir
    out_dir.mkdir(parents=True, exist_ok=True)

    # First construction downloads/caches the official Supertonic-3 model and
    # its built-in voice styles when they are not already present.
    tts = TTS(auto_download=True)

    cache_root = Path.home() / ".cache" / "supertonic3" / "voice_styles"
    f1_path = cache_root / "F1.json"
    f5_path = cache_root / "F5.json"
    if not f1_path.is_file() or not f5_path.is_file():
        raise FileNotFoundError(
            f"voice styles F1/F5 não encontrados em {cache_root}; "
            "o runtime Supertonic não populou o cache esperado"
        )

    styles_dir = out_dir / "styles"
    mixed_paths = generate_candidates(
        primary_path=f1_path,
        secondary_path=f5_path,
        output_dir=styles_dir,
    )

    candidates: list[tuple[str, object]] = [
        ("f1_baseline", tts.get_voice_style("F1")),
        ("f5_reference", tts.get_voice_style("F5")),
    ]
    for path in mixed_paths:
        candidates.append((path.stem, tts.get_voice_style_from_path(path)))

    for index, phrase in enumerate(PHRASES, start=1):
        phrase_dir = out_dir / f"phrase_{index:02d}"
        phrase_dir.mkdir(parents=True, exist_ok=True)
        (phrase_dir / "text.txt").write_text(phrase + "\n", encoding="utf-8")
        for label, style in candidates:
            wav, _duration = tts.synthesize(
                phrase,
                voice_style=style,
                lang="pt",
                total_step=args.steps,
                speed=args.speed,
            )
            tts.save_audio(wav, phrase_dir / f"{label}.wav")
            print(phrase_dir / f"{label}.wav")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
