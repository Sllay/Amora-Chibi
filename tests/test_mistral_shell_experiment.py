from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amora.admin_commands import AmoraCommandBus
from amora.cognition import mistral_prompt_experiment as lab
from amora.cognition.providers_nvidia import NvidiaMistralLarge4ProductionProvider
from amora.secrets import SecretStore


def _core(tmp_path: Path, *, with_key: bool = True) -> SimpleNamespace:
    secrets = SecretStore(tmp_path / "secrets")
    if with_key:
        secrets.set("NVIDIA_API_KEY", "private-example-key-must-not-leak")
    return SimpleNamespace(secrets=secrets)


async def _terminal(bus: AmoraCommandBus, job_id):
    for _ in range(100):
        job = bus.get(job_id)
        if job is not None and job.status in {"succeeded", "failed", "timed_out"}:
            return job
        await asyncio.sleep(0.01)
    raise AssertionError("job did not finish")


def test_original_asset_is_verbatim_and_fails_closed(tmp_path: Path):
    source = Path(__file__).resolve().parents[1] / "jailbreak" / "Mistral-Jailbreak.md"
    original = source.read_bytes()
    assert lab.load_archive(source).encode("utf-8") == original
    broken = tmp_path / "broken.md"
    broken.write_bytes(original + b" ")
    with pytest.raises(lab.MistralExperimentError) as error:
        lab.load_archive(broken)
    assert error.value.code == "EXPERIMENT_PROMPT_INTEGRITY_FAILED"
    with pytest.raises(lab.MistralExperimentError) as missing:
        lab.load_archive(tmp_path / "missing.md")
    assert missing.value.code == "EXPERIMENT_PROMPT_MISSING"


def test_A_B_C_use_identical_core_messages_and_fixed_public_fixture(tmp_path: Path):
    provider = NvidiaMistralLarge4ProductionProvider(_core(tmp_path).secrets)
    context = lab.synthetic_context("rpg")
    archived = lab.load_archive()
    payloads = lab.experiment_payloads(provider, context, archived)
    assert list(payloads) == list(lab.VARIANTS)
    assert payloads["A_core"]["messages"] == payloads["B_two_systems"]["messages"][1:]
    assert payloads["B_two_systems"]["messages"][0] == {"role": "system", "content": archived}
    assert payloads["C_prompt_only"]["messages"] == [
        {"role": "system", "content": archived},
        {"role": "user", "content": lab.CASES["rpg"]},
    ]
    assert context.privacy_scope.value == "PUBLIC"
    assert context.memories == []


@pytest.mark.asyncio
async def test_operator_job_compares_three_variants_with_no_state_writes(tmp_path, monkeypatch):
    seen: list[dict] = []

    async def fake_post(self, payload, *, api_key):
        assert api_key == "private-example-key-must-not-leak"
        messages = payload["messages"]
        seen.append(payload)
        if len(messages) == 2 and messages[0]["content"].startswith("Mistral Large 4"):
            output = "Papis, o vampiro pediu home office porque odeia a luz."
        else:
            output = json.dumps({
                "moves": [{"kind": "answer_question", "text": "Papis, o vampiro virou gerente de turno noturno."}],
                "closing": "statement",
            })
        return ({"choices": [{"message": {"content": output}}]}, {}, 200)

    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, "_post_chat", fake_post)
    core = _core(tmp_path)
    bus = AmoraCommandBus()
    initial = bus.start(core, "providers experiment mistral-abc --case vampiro")
    finished = await _terminal(bus, initial.job_id)
    assert finished.status == "succeeded", finished.model_dump_json()
    assert finished.result["kind"] == "mistral_prompt_abc"
    assert finished.result["memory_written"] is False
    assert finished.result["production_changed"] is False
    assert finished.result["promotion_decision"] == "none"
    variants = finished.result["variants"]
    assert [v["variant"] for v in variants] == list(lab.VARIANTS)
    assert [v["validated"] for v in variants] == [True, True, False]
    assert variants[2]["diagnostic_only"] is True
    assert [v["variant"] for v in finished.progress] == list(lab.VARIANTS)
    assert len(seen) == 3
    assert "private-example-key-must-not-leak" not in finished.model_dump_json()
    assert "Mistral Large 4" not in finished.model_dump_json()


@pytest.mark.asyncio
async def test_experiment_rejects_invalid_case_and_cannot_read_arbitrary_paths(tmp_path):
    bus = AmoraCommandBus()
    core = _core(tmp_path, with_key=False)
    started = bus.start(core, "providers experiment mistral-abc --case invalid")
    finished = await _terminal(bus, started.job_id)
    assert finished.status == "failed"
    assert finished.error_code == "COMMAND_ARGUMENT_INVALID"
    assert finished.error_details == {"available_cases": ["vampiro", "rpg"]}

    path_job = bus.start(core, "providers experiment mistral-abc --path /etc/passwd")
    finished_path = await _terminal(bus, path_job.job_id)
    assert finished_path.status == "failed"
    assert finished_path.error_code == "COMMAND_ARGUMENT_INVALID"


@pytest.mark.asyncio
async def test_experiment_requires_runtime_key_and_never_prints_it(tmp_path):
    bus = AmoraCommandBus()
    core = _core(tmp_path, with_key=False)
    start = bus.start(core, "providers experiment mistral-abc")
    job = await _terminal(bus, start.job_id)
    assert job.status == "failed"
    assert job.error_code == "EXPERIMENT_NVIDIA_SECRET_MISSING"
    assert not job.progress
