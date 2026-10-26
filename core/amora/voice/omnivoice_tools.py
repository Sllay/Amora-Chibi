"""Explicit CPU laboratory commands. No deployment, GPU or billing integration."""

from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import platform
import tempfile
from time import perf_counter

from .contract import RenderInput
from amora.conversation.models import SpeechStyle
from .omnivoice_renderer import (
    MODEL_ID,
    OmniVoiceRenderer,
    OmniVoiceSettings,
    load_official_model,
)
from .reference import OmniVoiceReferenceManifest, file_sha256

CORPUS = {
    "short": "Oi, pai. Tô aqui.",
    "medium": "Eu tava pensando naquele passeio. A gente pode conversar um pouquinho antes de sair?",
    "long": "Hoje eu quero ficar por aqui e conversar com calma. Tem tanta coisa pequena que deixa o dia mais bonito, tipo ouvir uma história, rir de uma bobagem ou só fazer companhia. Me conta o que aconteceu por aí.",
}


def create_prompt(
    reference: Path,
    text_file: Path,
    voice_id: str,
    manifest_path: Path,
    prompt_path: Path,
):
    """Encode once using the official API; manifest and prompt remain private."""
    if manifest_path.exists() or prompt_path.exists():
        raise ValueError("VOICE_ASSET_ALREADY_EXISTS")
    text = text_file.read_text(encoding="utf-8").strip()
    # Validate acoustic bounds before allocating the model, never invoke ASR.
    import wave

    with wave.open(str(reference), "rb") as wav:
        rate, duration = wav.getframerate(), wav.getnframes() / wav.getframerate()
    manifest = OmniVoiceReferenceManifest(
        voice_id=voice_id,
        reference_audio=str(reference.resolve()),
        reference_text=text,
        sha256=file_sha256(reference, maximum=2 * 1024 * 1024),
        sample_rate=rate,
        duration_seconds=duration,
        prompt_sha256="0" * 64,
    )
    manifest.validate_reference(manifest_path)
    directory = os.getenv("AMORA_OMNIVOICE_MODEL_DIRECTORY")
    settings = OmniVoiceSettings(
        manifest_path,
        prompt_path,
        model_directory=Path(directory) if directory else None,
    )
    model = load_official_model(settings)
    prompt = model.create_voice_clone_prompt(
        ref_audio=str(reference.resolve()), ref_text=text
    )
    if not prompt.ref_text.strip():
        raise ValueError("VOICE_REFERENCE_TRANSCRIPT_INVALID")
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    # Temp file is private from creation. Do not leave partial prompts as ready.
    fd, temporary = tempfile.mkstemp(dir=prompt_path.parent, suffix=".pt")
    os.close(fd)
    temporary = Path(temporary)
    try:
        prompt.save(str(temporary))
        # The official API may add terminal punctuation; manifest binds the exact
        # prompt transcript as well as the unchanged reference file's checksum.
        manifest = manifest.model_copy(
            update={
                "reference_text": prompt.ref_text,
                "prompt_sha256": file_sha256(temporary),
            }
        )
        temporary.replace(prompt_path)
        descriptor = os.open(manifest_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(manifest.model_dump_json(indent=2) + "\n")
        manifest.validate_files(manifest_path, prompt_path)
    finally:
        temporary.unlink(missing_ok=True)


def peak_rss_mb():
    try:
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return peak / (1024 * 1024 if platform.system() == "Darwin" else 1024)
    except (ImportError, OSError):
        return None


def benchmark(renderer: OmniVoiceRenderer, *, repeats=2):
    if not 1 <= repeats <= 10 or not renderer.ready():
        raise ValueError("OMNIVOICE_BENCHMARK_NOT_READY")
    report = {
        "schema": "amora.omnivoice.cpu-benchmark.v1",
        "measured_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL_ID,
        "package": "omnivoice==0.2.1",
        "device": "cpu",
        "dtype": "float32",
        "streaming_capable": False,
        "zero_cost_required": True,
        "threads": renderer.settings.threads,
        "host_cpu_count": os.cpu_count(),
        "model_load_seconds": renderer.model_load_seconds,
        "prompt_load_seconds": renderer.prompt_load_seconds,
        "first_render_seconds": None,
        "rows": [],
    }
    original = renderer.settings
    try:
        for steps in (16, 24, 32):
            renderer.settings = replace(original, num_step=steps)
            for name, text in CORPUS.items():
                runs = []
                for _ in range(repeats + 1):
                    start = perf_counter()
                    audio = renderer.render(RenderInput(text, SpeechStyle()))
                    seconds = perf_counter() - start
                    if report["first_render_seconds"] is None:
                        report["first_render_seconds"] = seconds
                    runs.append(
                        {
                            "render_seconds": seconds,
                            "audio_duration_seconds": audio.duration_seconds,
                            "rtf": seconds / audio.duration_seconds,
                            "peak_rss_mb": peak_rss_mb(),
                        }
                    )
                report["rows"].append(
                    {
                        "corpus": name,
                        "num_step": steps,
                        "first_render_seconds": runs[0]["render_seconds"],
                        "warm_render_seconds": [r["render_seconds"] for r in runs[1:]],
                        "runs": runs,
                    }
                )
    finally:
        renderer.settings = original
    report["peak_rss_mb"] = peak_rss_mb()
    return report


def save_benchmark(report, destination: Path):
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "benchmark.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
    lines = [
        "# OmniVoice CPU — medições locais",
        "",
        "float32, modelo oficial, sem streaming; estes números pertencem ao host medido.",
        "",
        f"Model load: {report['model_load_seconds']} s; prompt load: {report['prompt_load_seconds']} s.",
        "",
        "| Corpus | Steps | First (s) | Warm (s) | Duration (s) | RTF | Peak RSS (MiB) |",
        "|---|---:|---:|---|---:|---:|---:|",
    ]
    for row in report["rows"]:
        run = row["runs"][-1]
        warm = ", ".join(f"{v:.3f}" for v in row["warm_render_seconds"])
        lines.append(
            f"| {row['corpus']} | {row['num_step']} | {row['first_render_seconds']:.3f} | {warm} | {run['audio_duration_seconds']:.3f} | {run['rtf']:.3f} | {run['peak_rss_mb']} |"
        )
    (destination / "benchmark.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    download = commands.add_parser("download")
    download.add_argument("--revision", default="main")
    download.add_argument("--path-only", action="store_true")
    create = commands.add_parser("create-prompt")
    create.add_argument("--reference", type=Path, required=True)
    create.add_argument("--reference-text-file", type=Path, required=True)
    create.add_argument("--voice-id", required=True)
    create.add_argument("--manifest", type=Path, required=True)
    create.add_argument("--prompt", type=Path, required=True)
    bench = commands.add_parser("benchmark")
    bench.add_argument("--output", type=Path, required=True)
    bench.add_argument("--repeats", type=int, default=2)
    args = parser.parse_args()
    try:
        if args.command == "download":
            from huggingface_hub import snapshot_download

            snapshot = Path(snapshot_download(MODEL_ID, revision=args.revision))
            if not (snapshot / "audio_tokenizer").is_dir():
                snapshot_download("eustlb/higgs-audio-v2-tokenizer")
            print(
                str(snapshot)
                if args.path_only
                else "Official model cached. No synthesis, GPU or deployment performed."
            )
        elif args.command == "create-prompt":
            create_prompt(
                args.reference,
                args.reference_text_file,
                args.voice_id,
                args.manifest,
                args.prompt,
            )
            print(
                "Private reference manifest and reusable prompt created; no canonical voice approved."
            )
        else:
            renderer = OmniVoiceRenderer(OmniVoiceSettings.from_environment())
            save_benchmark(benchmark(renderer, repeats=args.repeats), args.output)
            print(
                "CPU benchmark complete; JSON and Markdown saved without reference content."
            )
    except Exception as exc:
        # No raw library exception, paths, tokens or transcript on stdout/stderr.
        parser.exit(1, f"OMNIVOICE_LAB_COMMAND_FAILED ({type(exc).__name__})\n")


if __name__ == "__main__":
    main()
