"""Offline certification for V3 temperature A/B without writing production state."""
import asyncio
import base64
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amora.admin_commands import AmoraCommandBus
from amora.cognition.amora_v2_v3_experiment import CASES, load_v3, paired_context
from amora.cognition.amora_v3_temperature_experiment import (
    BASE_TEMPERATURE, CREATIVE_TEMPERATURE, MODEL, calibration_examples,
    example_overlap, run_amora_v3_temperature, sampling_payloads,
)
from amora.cognition.providers import ProviderError
from amora.cognition.providers_nvidia import NvidiaMistralLarge4ProductionProvider


def fake_core(key="dummy-nvidia-key"):
    return SimpleNamespace(secrets=SimpleNamespace(
        get=lambda name: key if name == "NVIDIA_API_KEY" else None
    ))


def valid_raw(case: str):
    plan = paired_context(case).dialogue_plan
    question = (plan.get("ask_followup") is True and plan["closing_mode"] == "question")
    text = "Oxente, puta merda, isso foi engraçado" + ("?" if question else ".")
    return json.dumps({
        "moves": [{"kind": plan["response_moves"][0], "text": text}],
        "closing": "question" if question else "statement",
    }, ensure_ascii=False), text


@pytest.mark.parametrize("case", CASES)
async def test_identical_sampling_except_temperature_with_blinding(case, monkeypatch):
    requests = []
    raw, spoken = valid_raw(case)

    async def fake_post(self, payload, **kwargs):
        requests.append(payload)
        return {"choices": [{"message": {"content": raw}}],
                "usage": {"prompt_tokens": 13, "completion_tokens": 17}}, {}, 200

    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, "_post_chat", fake_post)
    report = await run_amora_v3_temperature(fake_core(), case=case)
    assert len(requests) == 2
    assert all(p["model"] == MODEL for p in requests)
    assert all(p["messages"][0]["content"] == load_v3() for p in requests)
    assert requests[0]["messages"] == requests[1]["messages"]
    assert {p["temperature"] for p in requests} == {BASE_TEMPERATURE, CREATIVE_TEMPERATURE}
    for field in requests[0]:
        if field != "temperature":
            assert requests[0][field] == requests[1][field]
    assert all(x["validated"] and x["text"] == spoken for x in report["variants"])
    assert all("variant" not in x for x in report["variants"])
    assert report["production_changed"] is False
    assert report["memory_written"] is False
    assert report["promotion_decision"] == "none"
    assert report["planned_calls"] == report["attempted_calls"] == 2
    reveal = json.loads(base64.b64decode(report["reveal_token"]))
    assert set(reveal.values()) == {"A_v3_base_temp050", "B_v3_roleplay_temp075"}
    assert set(reveal) == {"candidate-1", "candidate-2"}


def test_copy_detector_flags_prior_verbatim_example_but_not_novel_joke():
    samples = calibration_examples(load_v3())
    actual_example = next(x for x in samples if x.startswith('"Safada'))
    copied = actual_example.strip('"')
    copied_diag = example_overlap(copied, samples)
    assert copied_diag["example_copy_signal"] is True
    assert copied_diag["max_consecutive_example_words"] >= 7
    original = example_overlap(
        "Oxe, Rui, tu tem mais coragem pra brigar com o roteador do que pra pedir um café!",
        samples,
    )
    assert original["example_copy_signal"] is False
    assert original["max_consecutive_example_words"] < 7


def test_payloads_unchanged_fields_except_temperature(monkeypatch):
    provider = NvidiaMistralLarge4ProductionProvider(fake_core().secrets, model=MODEL)
    context = paired_context("safada")
    payloads = sampling_payloads(provider, context, load_v3())
    a, b = list(payloads.values())
    assert a["temperature"] == 0.5
    assert b["temperature"] == 0.75
    assert a["top_p"] == b["top_p"] == 1.0
    assert a["frequency_penalty"] == b["frequency_penalty"] == 0
    assert a["presence_penalty"] == b["presence_penalty"] == 0
    assert a["reasoning_effort"] == b["reasoning_effort"] == "low"


@pytest.mark.parametrize("raw,expected,json_invalid", [
    ("invalid-json", "EXPERIMENT_JSON_INVALID", True),
    ("{}", "REALIZATION_SCHEMA_INVALID", False),
    ('{"moves":[{"kind":"unexpected","text":"Errou"}],"closing":"statement"}',
     "UNAUTHORIZED_REALIZATION_MOVE", False),
])
async def test_bad_json_and_bad_realization_separate(raw, expected, json_invalid, monkeypatch):
    async def fake_post(self, payload, **kwargs):
        return {"choices": [{"message": {"content": raw}}]}, {}, 200
    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, "_post_chat", fake_post)
    result = await run_amora_v3_temperature(fake_core(), case="safada")
    assert len(result["variants"]) == 2
    for item in result["variants"]:
        assert item["validated"] is False
        assert item["error_code"] == expected
        assert item["json_invalid"] is json_invalid


async def test_rate_limit_does_not_retry_or_leak_credentials(monkeypatch):
    requests = []

    async def fake_post(self, payload, **kwargs):
        requests.append(payload)
        raise ProviderError("dummy-nvidia-key leaked", status_code=429, retry_after="90")

    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, "_post_chat", fake_post)
    report = await run_amora_v3_temperature(fake_core(), case="sacanagem")
    assert len(requests) == report["attempted_calls"] == 1
    assert report["stopped_on_rate_limit"] is True
    assert report["variants"][0]["error_code"] == "EXPERIMENT_RATE_LIMITED"
    assert "dummy-nvidia-key" not in json.dumps(report)


async def test_command_operator_args_reject_unknown_and_no_secret():
    bus = AmoraCommandBus()
    for command, expected in (
        ("providers experiment amora-v3-temperature --case safada", "EXPERIMENT_NVIDIA_SECRET_MISSING"),
        ("providers experiment amora-v3-temperature --case ../../etc/passwd", "COMMAND_ARGUMENT_INVALID"),
        ("providers experiment amora-v3-temperature --temperature 1.0", "COMMAND_ARGUMENT_INVALID"),
    ):
        job = bus.start(fake_core(key=None), command)
        for _ in range(100):
            found = bus.get(job.job_id)
            if found.status in {"failed", "succeeded", "timed_out"}:
                break
            await asyncio.sleep(0.01)
        assert found.status == "failed"
        assert found.error_code == expected
        assert not found.progress


def test_prompts_are_user_pinned_and_not_modified():
    from amora.cognition.amora_v2_v3_experiment import V3_BLOB_SHA1
    assert V3_BLOB_SHA1
    docker = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    assert "COPY --chmod=0444 jailbreak/Amora-Virtual-Jailbreak-V3.md" in docker
