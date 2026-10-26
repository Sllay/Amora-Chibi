"""Laboratory tooling contract only; synthetic mocks are never real benchmarks."""

import json

import pytest

from amora.voice import omnivoice_tools
from amora.voice.reference import OmniVoiceReferenceManifest
import test_omnivoice_renderer

mocked_omni = test_omnivoice_renderer.mocked_omni
reference_assets = test_omnivoice_renderer.reference_assets


def test_benchmark_exercises_all_steps_with_one_loaded_model_and_no_reference_content(
    mocked_omni, reference_assets, tmp_path
):
    renderer, model, loads = mocked_omni
    report = omnivoice_tools.benchmark(renderer, repeats=1)
    assert len(report["rows"]) == 9
    assert loads == {"model": 1, "prompt": 1}
    assert [call["num_step"] for call in model.calls] == [16] * 6 + [24] * 6 + [32] * 6
    assert renderer.settings.num_step == 32
    assert all(r["runs"][0]["audio_duration_seconds"] == 0.1 for r in report["rows"])
    omnivoice_tools.save_benchmark(report, tmp_path / "benchmark")
    assert "reference" not in json.dumps(report)
    assert (
        reference_assets[3]["reference_text"]
        not in (tmp_path / "benchmark" / "benchmark.md").read_text()
    )
    assert not any(
        value in json.dumps(report)
        for value in [
            str(reference_assets[1]),
            reference_assets[3]["sha256"],
            "prompt.pt",
        ]
    )


def test_prompt_creation_uses_official_api_once_and_private_files(
    reference_assets, monkeypatch, tmp_path
):
    source_manifest, _, reference, data = reference_assets
    transcript = tmp_path / "transcript.txt"
    transcript.write_text(data["reference_text"], encoding="utf-8")
    calls = []

    class Prompt:
        ref_text = data["reference_text"]

        def save(self, path):
            from pathlib import Path

            calls.append("save")
            Path(path).write_bytes(b"official-prompt-fixture")

    class Model:
        def create_voice_clone_prompt(self, **kwargs):
            assert kwargs == {
                "ref_audio": str(reference.resolve()),
                "ref_text": data["reference_text"],
            }
            calls.append("create")
            return Prompt()

    def load(settings):
        calls.append("load")
        return Model()

    monkeypatch.setattr(omnivoice_tools, "load_official_model", load)
    manifest, prompt = tmp_path / "new-manifest.json", tmp_path / "new-prompt.pt"
    omnivoice_tools.create_prompt(
        reference, transcript, "LAB_OWN_REFERENCE_V1", manifest, prompt
    )
    result = OmniVoiceReferenceManifest.load(manifest)
    result.validate_files(manifest, prompt)
    assert calls == ["load", "create", "save"]
    assert manifest.stat().st_mode & 0o777 == 0o600
    assert prompt.stat().st_mode & 0o777 == 0o600
    with pytest.raises(ValueError, match="ALREADY_EXISTS"):
        omnivoice_tools.create_prompt(
            reference, transcript, "LAB_OWN_REFERENCE_V1", manifest, prompt
        )
    assert calls == ["load", "create", "save"]
