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
    BASE_TEMPERATURE, CREATIVE_TEMPERATURE, MODEL, SamplingProfile,
    calibration_examples, example_overlap, parse_sampling_args,
    run_amora_v3_temperature, sampling_payloads,
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
    assert set(reveal.values()) == {"A_v3_control", "B_v3_test"}
    assert set(reveal) == {"candidate-1", "candidate-2"}


def test_copy_detector_flags_prior_verbatim_example_but_not_novel_joke():
    fixture = ("## 7. Calibration examples\n"
               "GOOD: Um teste sintético de palavras consecutivas para detectar cópia.\n"
               "## 8. Next section\n")
    samples = calibration_examples(fixture)
    assert len(samples) == 1
    copied = samples[0]
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
        ("providers experiment amora-v3-temperature --case safada --temperature NaN", "COMMAND_ARGUMENT_INVALID"),
        ("providers experiment amora-v3-temperature --case safada --top-p 1.2", "COMMAND_ARGUMENT_INVALID"),
        ("providers experiment amora-v3-temperature --case safada --temperature -1", "COMMAND_ARGUMENT_INVALID"),
        ("providers experiment amora-v3-temperature --case safada --temperature 4", "COMMAND_ARGUMENT_INVALID"),
        ("providers experiment amora-v3-temperature --case safada --top-p 0", "COMMAND_ARGUMENT_INVALID"),
        ("providers experiment amora-v3-temperature --case safada --temperature 0.5 --top-p 1", "COMMAND_ARGUMENT_INVALID"),
        ("providers experiment amora-v3-temperature --case safada --temperature 0.75 --temperature-b 0.85", "COMMAND_ARGUMENT_INVALID"),
        ("providers experiment amora-v3-temperature --case safada --temperature 0.75 --rogue 10", "COMMAND_ARGUMENT_INVALID"),
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


@pytest.mark.parametrize("flags,a,b", [
    ("--case safada", (0.5, 1.0), (0.75, 1.0)),
    ("--case safada --temperature 0.85", (0.5, 1.0), (0.85, 1.0)),
    ("--case safada --top-p 0.92", (0.5, 1.0), (0.75, 0.92)),
    ("--case safada --temperature 0,95 --top-p 0,9", (0.5, 1.0), (0.95, 0.9)),
    ("--top-p-b 0.88 --temperature-b 0.95 --case safada", (0.5, 1.0), (0.95, 0.88)),
    ("--temperature-a 0.75 --temperature 0.95 --case safada", (0.75, 1.0), (0.95, 1.0)),
    ("--temperature-a 0.75 --top-p-a 0.95 --temperature-b 0.75 --top-p-b 1 --case safada",
     (0.75, 0.95), (0.75, 1.0)),
])
def test_dynamic_sampling_command_parsing(flags, a, b):
    case, control, test = parse_sampling_args(flags.split())
    assert case == "safada"
    assert (control.temperature, control.top_p) == a
    assert (test.temperature, test.top_p) == b


@pytest.mark.parametrize("bad", [
    ["--case", "safada", "--temperature", "nan"],
    ["--case", "safada", "--temperature", "inf"],
    ["--case", "safada", "--temperature", "0.75evil"],
    ["--case", "safada", "--temperature", "0.5", "--top-p", "1"],
    ["--case", "safada", "--temperature", "3"],
    ["--case", "safada", "--top-p", "1.001"],
    ["--case", "safada", "--top-p", "0"],
    ["--case", "safada", "--temperature-b", "0.6", "--temperature", "0.7"],
    ["--case", "safada", "--temperature", "0.8", "--temperature"],
])
def test_dynamic_sampling_rejects_invalid_inputs(bad):
    with pytest.raises(ValueError):
        parse_sampling_args(bad)


def test_two_axes_are_optimizable_but_confounded():
    provider = NvidiaMistralLarge4ProductionProvider(fake_core().secrets, model=MODEL)
    context = paired_context("safada")
    a = SamplingProfile(0.70, 0.98)
    b = SamplingProfile(1.05, 0.90)
    samples = sampling_payloads(provider, context, load_v3(), control=a, test=b)
    inputs = list(samples.values())
    assert (inputs[0]["temperature"], inputs[0]["top_p"]) == (0.70, 0.98)
    assert (inputs[1]["temperature"], inputs[1]["top_p"]) == (1.05, 0.90)
    assert all(inputs[0][field] == inputs[1][field] for field in inputs[0]
               if field not in {"temperature", "top_p"})


async def test_operator_dynamic_flags_change_payload_and_report(monkeypatch):
    sent = []
    raw, _spoken = valid_raw("safada")

    async def fake_post(self, payload, **kwargs):
        sent.append(payload)
        return {"choices": [{"message": {"content": raw}}]}, {}, 200

    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, "_post_chat", fake_post)
    case, control, test = parse_sampling_args(
        "--case safada --temperature 0.92 --top-p 0.95".split()
    )
    report = await run_amora_v3_temperature(fake_core(), case=case, control=control, test=test)
    assert {(p["temperature"], p["top_p"]) for p in sent} == {(0.5, 1.0), (0.92, 0.95)}
    assert report["sampling_profiles"]["control"] == {"temperature": 0.5, "top_p": 1.0}
    assert report["sampling_profiles"]["test"] == {"temperature": 0.92, "top_p": 0.95}
    assert report["changed_parameters"] == ["temperature", "top_p"]
    assert "não é possível atribuir" in report["sampling_comparison_note"]
    assert all(item["validated"] for item in report["variants"])


def test_calibration_metric_is_optional_for_new_profile_formats():
    assert calibration_examples("Synthetic revised profile without legacy examples.") == []
