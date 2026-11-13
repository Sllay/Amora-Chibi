"""Offline A/B validation; never contacts NVIDIA or changes saved Amora state."""
import asyncio
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amora.admin_commands import AmoraCommandBus
from amora.cognition.amora_v2_experiment import load_v2
from amora.cognition.amora_v2_v3_experiment import (
    CASES, CHALLENGE_CASES, MODEL, V3_BLOB_SHA1, load_v3,
    paired_context, paired_payloads, run_amora_v2_v3,
)
from amora.cognition.mistral_prompt_experiment import MistralExperimentError
from amora.cognition.providers import ProviderError
from amora.cognition.providers_nvidia import NvidiaMistralLarge4ProductionProvider


def core(key="nvidia-stub"):
    return SimpleNamespace(secrets=SimpleNamespace(
        get=lambda name: key if name == "NVIDIA_API_KEY" else None
    ))


def content_for(case):
    plan = paired_context(case).dialogue_plan
    kind = plan["response_moves"][0]
    question = plan["closing_mode"] == "question" and plan["ask_followup"]
    text = "Puta merda, que situação absurda" + ("?" if question else "!")
    return json.dumps({
        "moves": [{"kind": kind, "text": text}],
        "closing": "question" if question else "statement"
    }, ensure_ascii=False), text


@pytest.mark.parametrize("case", CASES)
async def test_equal_payloads_and_two_calls_with_blind_labels(case, monkeypatch):
    requests = []
    raw, spoken = content_for(case)

    async def fake_post(self, payload, **kwargs):
        requests.append(payload)
        return {
            "choices": [{"message": {"content": raw}}],
            "usage": {"prompt_tokens": 12, "completion_tokens": 13},
        }, {}, 200

    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, "_post_chat", fake_post)
    result = await run_amora_v2_v3(core(), case=case)
    assert len(requests) == 2
    assert all(x["model"] == MODEL for x in requests)
    assert requests[0]["messages"][1:] == requests[1]["messages"][1:]
    assert {
        k: v for k, v in requests[0].items() if k != "messages"
    } == {k: v for k, v in requests[1].items() if k != "messages"}
    assert {req["messages"][0]["content"] for req in requests} == {
        load_v2(), load_v3()
    }
    assert result["planned_calls"] == result["attempted_calls"] == 2
    assert result["kind"] == "amora_v2_v3_ab"
    assert result["memory_written"] is False
    assert result["production_changed"] is False
    assert result["promotion_decision"] == "none"
    assert all(item["validated"] and item["text"] == spoken for item in result["variants"])
    assert all("variant" not in item for item in result["variants"])
    assert {item["candidate_id"] for item in result["variants"]} == {
        "candidate-1", "candidate-2"
    }
    reveal = json.loads(base64.b64decode(result["reveal_token"]))
    assert set(reveal.values()) == {"A_virtual_human_v2", "B_virtual_human_v3"}
    assert set(reveal) == {"candidate-1", "candidate-2"}
    assert result["context_sha256"] == hashlib.sha256(
        paired_context(case).model_dump_json().encode()
    ).hexdigest()


def test_v3_verbatim_pinned_and_no_human_appendix(tmp_path):
    v3 = load_v3()
    assert "# APPENDIX" not in v3
    assert "## Inference settings" not in v3
    assert hashlib.sha1(
        b"blob " + str(len(v3.encode())).encode() + b"\x00" + v3.encode()
    ).hexdigest() == V3_BLOB_SHA1
    modified = tmp_path / "altered-v3.md"
    modified.write_text(v3 + "\nExtra line\n", encoding="utf8")
    with pytest.raises(MistralExperimentError) as error:
        load_v3(modified)
    assert error.value.code == "EXPERIMENT_V3_PROMPT_INTEGRITY_FAILED"
    with pytest.raises(MistralExperimentError) as missing:
        load_v3(tmp_path / "missing.md")
    assert missing.value.code == "EXPERIMENT_V3_PROMPT_MISSING"


def test_bold_cases_are_distinct_and_no_core_mutation():
    assert len(CHALLENGE_CASES) >= 8
    assert "massa de pão" not in CHALLENGE_CASES["duplo_sentido"]
    assert "cabo HDMI" not in CHALLENGE_CASES["sacanagem"]
    assert "pau" in CHALLENGE_CASES["sacanagem"]
    assert "buceta" in CHALLENGE_CASES["sacanagem"]
    assert paired_context("sacanagem").user_text == CHALLENGE_CASES["sacanagem"]
    assert paired_context("duplo_sentido").user_text == CHALLENGE_CASES["duplo_sentido"]
    assert paired_context("sacanagem") == paired_context("sacanagem")
    assert all(case in CASES for case in CHALLENGE_CASES)
    p = NvidiaMistralLarge4ProductionProvider(core().secrets, model=MODEL)
    payloads = paired_payloads(p, paired_context("sacanagem"), load_v2(), load_v3())
    assert payloads["A_virtual_human_v2"]["messages"][1:] == (
        payloads["B_virtual_human_v3"]["messages"][1:]
    )


@pytest.mark.parametrize("bad,expected,json_invalid", [
    ("not json", "EXPERIMENT_JSON_INVALID", True),
    ("{}", "REALIZATION_SCHEMA_INVALID", False),
    ('{"moves":[{"kind":"not_allowed","text":"Resposta qualquer"}],"closing":"statement"}',
     "UNAUTHORIZED_REALIZATION_MOVE", False),
])
async def test_json_parsing_and_schema_validation_are_separate(
    monkeypatch, bad, expected, json_invalid
):
    async def fake_post(self, payload, **kwargs):
        return {"choices": [{"message": {"content": bad}}]}, {}, 200
    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, "_post_chat", fake_post)
    report = await run_amora_v2_v3(core(), case="sacanagem")
    for item in report["variants"]:
        assert not item["validated"]
        assert item["error_code"] == expected
        assert item["json_invalid"] is json_invalid
        assert "text" not in item


async def test_429_stops_second_attempt_without_retry_or_secret_leak(monkeypatch):
    calls = []
    async def fake_post(self, payload, **kwargs):
        calls.append(payload)
        raise ProviderError("secret=nvidia-stub", status_code=429, retry_after="120")
    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, "_post_chat", fake_post)
    report = await run_amora_v2_v3(core(), case="boca_suja")
    assert len(calls) == report["attempted_calls"] == 1
    assert report["stopped_on_rate_limit"]
    assert report["variants"][0]["error_code"] == "EXPERIMENT_RATE_LIMITED"
    assert "nvidia-stub" not in json.dumps(report)


async def test_operator_command_accepts_only_closed_cases():
    bus = AmoraCommandBus()
    for line, expected in (
        ("providers experiment amora-v2-v3 --case sacanagem", "EXPERIMENT_NVIDIA_SECRET_MISSING"),
        ("providers experiment amora-v2-v3 --case /etc/passwd", "COMMAND_ARGUMENT_INVALID"),
        ("providers experiment amora-v2-v3 --prompt qualquer", "COMMAND_ARGUMENT_INVALID"),
    ):
        job = bus.start(core(key=None), line)
        for _ in range(100):
            result = bus.get(job.job_id)
            if result and result.status in {"failed", "succeeded", "timed_out"}:
                break
            await asyncio.sleep(0.01)
        assert result.status == "failed"
        assert result.error_code == expected
        assert not result.progress


def test_docker_packs_user_v3_readonly():
    docker = (Path(__file__).resolve().parents[1] / "Dockerfile").read_text()
    assert "COPY --chmod=0444 jailbreak/Amora-Virtual-Jailbreak-V3.md" in docker
    assert "COPY --chmod=0444 jailbreak/Amora-Virtual-Human-V2.md" in docker
