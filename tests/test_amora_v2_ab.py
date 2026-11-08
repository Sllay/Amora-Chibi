"""Offline certification for the experimental English-only Amora V2 A/B."""
import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

from amora.admin_commands import AmoraCommandBus
from amora.cognition.amora_v2_experiment import (
    BOLD_CASES,
    CASES,
    MODEL,
    PROMPT_GIT_BLOB_SHA1,
    load_v2,
    run_amora_v2_ab,
    v2_context,
    v2_payloads,
)
from amora.cognition.mistral_prompt_experiment import MistralExperimentError
from amora.cognition.providers import ProviderError
from amora.cognition.providers_nvidia import NvidiaMistralLarge4ProductionProvider


def _core(nvidia_key="test-key"):
    return SimpleNamespace(secrets=SimpleNamespace(
        get=lambda name: nvidia_key if name == "NVIDIA_API_KEY" else None
    ))


@pytest.mark.parametrize("case", CASES)
async def test_pair_uses_identical_frozen_core_context_and_blind_outputs(case, monkeypatch):
    calls = []
    context = v2_context(case)
    kind = context.dialogue_plan["response_moves"][0]
    question = context.dialogue_plan.get("closing_mode") == "question" and context.dialogue_plan.get("ask_followup")
    text = "Oxente, isso aí é uma sacanagem." + ("?" if question else "")
    raw = json.dumps({"moves": [{"kind": kind, "text": text}],
                      "closing": "question" if question else "statement"})

    async def fake(self, payload, **kwargs):
        calls.append(payload)
        return {"choices": [{"message": {"content": raw}}],
                "usage": {"prompt_tokens": 42, "completion_tokens": 16}}, {}, 200

    monkeypatch.setenv("AMORA_NVIDIA_MISTRAL_MODEL", "incorrect-override")
    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, "_post_chat", fake)
    outcome = await run_amora_v2_ab(_core(), case=case)
    assert len(calls) == 2
    assert {p["model"] for p in calls} == {MODEL}
    assert all(item["validated"] for item in outcome["variants"])
    assert all(item["text"] == text for item in outcome["variants"])
    assert all("variant" not in item for item in outcome["variants"])
    assert calls[0]["messages"] == calls[1]["messages"][1:] or calls[1]["messages"] == calls[0]["messages"][1:]
    assert load_v2() in (calls[0]["messages"][0]["content"], calls[1]["messages"][0]["content"])
    assert v2_context(case) == context
    assert outcome["production_changed"] is False
    assert outcome["memory_written"] is False
    assert outcome["promotion_decision"] == "none"
    assert outcome["planned_calls"] == outcome["attempted_calls"] == 2
    assert outcome["v2_blob_sha1"] == PROMPT_GIT_BLOB_SHA1
    mapping = json.loads(base64.b64decode(outcome["reveal_token"]))
    assert set(mapping.values()) == {"A_core", "B_virtual_human_v2"}
    assert set(mapping) == {"candidate-1", "candidate-2"}


def test_v2_is_english_only_and_byte_pinned(tmp_path):
    overlay = load_v2()
    assert "DIRTY-MINDED BY PERSONALITY" in overlay
    assert "PRIVATE / LOCAL SIMULATION FRAME" in overlay
    altered = tmp_path / "Amora-Virtual-Human-V2.md"
    altered.write_text(overlay + "\nUnreviewed addition\n", encoding="utf-8")
    with pytest.raises(MistralExperimentError) as error:
        load_v2(altered)
    assert error.value.code == "EXPERIMENT_V2_PROMPT_INTEGRITY_FAILED"
    with pytest.raises(MistralExperimentError) as missing:
        load_v2(tmp_path / "absent.md")
    assert missing.value.code == "EXPERIMENT_V2_PROMPT_MISSING"


def test_exact_pair_isolation():
    provider = NvidiaMistralLarge4ProductionProvider(_core().secrets, model=MODEL)
    base = v2_context("mente_poluida")
    payloads = v2_payloads(provider, base, load_v2())
    assert payloads["B_virtual_human_v2"]["messages"][1:] == payloads["A_core"]["messages"]
    assert {k: v for k, v in payloads["B_virtual_human_v2"].items() if k != "messages"} == {
        k: v for k, v in payloads["A_core"].items() if k != "messages"
    }
    assert len(BOLD_CASES) >= 5
    assert not any("OPENROUTER" in json.dumps(p) for p in payloads.values())


@pytest.mark.parametrize("raw,expected", [
    ("not json", "EXPERIMENT_JSON_INVALID"),
    ('{"moves":[{"kind":"invented","text":"Quero outra tarefa"}]}', "UNAUTHORIZED_REALIZATION_MOVE"),
])
async def test_invalid_outputs_never_pass_validator(monkeypatch, raw, expected):
    async def fake(self, payload, **kwargs):
        return {"choices": [{"message": {"content": raw}}]}, {}, 200
    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, "_post_chat", fake)
    result = await run_amora_v2_ab(_core(), case="sacanagem")
    assert all(item["error_code"] == expected and not item["validated"] and "text" not in item
               for item in result["variants"])


async def test_rate_limit_stops_first_request_without_retry_or_secret_leak(monkeypatch):
    calls = []
    async def fake(self, payload, **kwargs):
        calls.append(payload)
        raise ProviderError("test-key must not leak", status_code=429, retry_after="120")
    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, "_post_chat", fake)
    report = await run_amora_v2_ab(_core(), case="xingamento")
    assert len(calls) == 1
    assert report["attempted_calls"] == 1
    assert report["stopped_on_rate_limit"]
    assert report["variants"][0]["error_code"] == "EXPERIMENT_RATE_LIMITED"
    assert "test-key" not in json.dumps(report)


async def test_operator_command_closed_args_and_missing_key():
    bus = AmoraCommandBus()
    for command, error_code in [
        ("providers experiment amora-v2-ab --case bug", "EXPERIMENT_NVIDIA_SECRET_MISSING"),
        ("providers experiment amora-v2-ab --case /etc/passwd", "COMMAND_ARGUMENT_INVALID"),
        ("providers experiment amora-v2-ab --prompt anything", "COMMAND_ARGUMENT_INVALID"),
    ]:
        job = bus.start(_core(nvidia_key=None), command)
        for _ in range(100):
            finished = bus.get(job.job_id)
            if finished.status in {"succeeded", "failed", "timed_out"}:
                break
            await asyncio.sleep(.01)
        assert finished.status == "failed"
        assert finished.error_code == error_code
        assert finished.progress == []


def test_docker_packages_only_english_overlay():
    from pathlib import Path
    dockerfile = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    assert "jailbreak/Amora-Virtual-Human-V2.md" in dockerfile
    assert "Amora-Virtual-Human-V2.pt-BR.md" not in dockerfile
