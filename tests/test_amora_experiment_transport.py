"""NVIDIA laboratory transport diagnostics: timeout is NOT a Core rejection."""
import json
from types import SimpleNamespace

import pytest

from amora.cognition.amora_experiment_transport import (
    EXPERIMENT_IDLE_SECONDS, EXPERIMENT_MAX_TOTAL_SECONDS,
    classify_provider_failure, experiment_provider,
)
from amora.cognition.amora_v2_v3_experiment import run_amora_v2_v3
from amora.cognition.amora_v3_temperature_experiment import run_amora_v3_temperature
from amora.cognition.providers import ProviderError
from amora.cognition.providers_nvidia import (
    NvidiaMistralLarge4ProductionProvider, NvidiaStreamTimeout,
)


def stub_core():
    secrets = SimpleNamespace(get=lambda name: "safe-secret" if name == "NVIDIA_API_KEY" else None)
    return SimpleNamespace(secrets=secrets)


def timeout_exception():
    return NvidiaStreamTimeout({
        "stage": "waiting_first_event",
        "timeout_kind": "idle",
        "elapsed_ms": 55088.0,
        "silence_ms": 55002.0,
        "first_event_ms": None,
        "first_visible_ms": None,
        "first_reasoning_ms": None,
        "reasoning_events": 0,
        "visible_events": 0,
        "prompt_chars": 25000,
        "message_count": 4,
        "visible_chars": 0,
        "private_text": "DON'T LEAK",
        "key": "safe-secret",
    })


def test_experiment_deadlines_are_bounded_and_production_defaults_unmodified():
    core = stub_core()
    lab = experiment_provider(core.secrets, model="mistralai/mistral-large-4-1t-a52b")
    prod = NvidiaMistralLarge4ProductionProvider(core.secrets)
    assert lab.timeout_seconds == EXPERIMENT_IDLE_SECONDS == 100.0
    assert lab.max_total_seconds == EXPERIMENT_MAX_TOTAL_SECONDS == 225.0
    assert prod.timeout_seconds == 55.0
    assert prod.max_total_seconds == 185.0
    assert EXPERIMENT_MAX_TOTAL_SECONDS * 2 < 650


def test_safe_timeout_diagnostics_do_not_expose_transport_content():
    result = {"validated": True}
    classify_provider_failure(result, timeout_exception())
    assert result["validated"] is False
    assert result["validation_status"] == "not_run"
    assert result["diagnostic_only"] is True
    assert result["error_code"] == "EXPERIMENT_NVIDIA_TIMEOUT"
    assert result["failure_stage"] == "waiting_first_event"
    assert result["timeout_kind"] == "idle"
    assert result["stream_diagnostics"]["visible_events"] == 0
    assert "safe-secret" not in json.dumps(result)
    assert "DON'T LEAK" not in json.dumps(result)
    assert "prompt_chars" not in json.dumps(result)


@pytest.mark.parametrize("status,expected", [
    (429, "EXPERIMENT_RATE_LIMITED"),
    (500, "EXPERIMENT_PROVIDER_FAILURE"),
    (503, "EXPERIMENT_PROVIDER_FAILURE"),
])
def test_http_failure_classified_as_transport_not_core(status, expected):
    item = {}
    classify_provider_failure(
        item,
        ProviderError("remote body could contain safe-secret", status_code=status),
    )
    assert item["error_code"] == expected
    assert item["http_status"] == status
    assert item["validation_status"] == "not_run"
    assert item["rate_limit_observed"] is (status == 429)
    assert "safe-secret" not in json.dumps(item)


@pytest.mark.parametrize("runner", [run_amora_v2_v3, run_amora_v3_temperature])
async def test_each_ab_variant_reports_instrumented_nvidia_timeout(runner, monkeypatch):
    requests = []

    async def fake_post(self, payload, **kwargs):
        requests.append({
            "timeout_seconds": self.timeout_seconds,
            "max_total_seconds": self.max_total_seconds,
            "payload": payload,
        })
        raise timeout_exception()

    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, "_post_chat", fake_post)
    report = await runner(stub_core(), case="sacanagem")
    assert len(requests) == 2
    assert all(req["timeout_seconds"] == 100.0 for req in requests)
    assert all(req["max_total_seconds"] == 225.0 for req in requests)
    assert report["attempted_calls"] == 2
    assert report["stopped_on_rate_limit"] is False
    assert report["transport_deadlines"] == {
        "idle_seconds": 100.0,
        "max_total_seconds": 225.0,
        "production_unchanged": True,
    }
    for variant in report["variants"]:
        assert variant["error_code"] == "EXPERIMENT_NVIDIA_TIMEOUT"
        assert variant["validation_status"] == "not_run"
        assert variant["dialogue_plan_fidelity"] is None
        assert not variant["validated"]
        assert variant["failure_stage"] == "waiting_first_event"
        assert "candidate_output" not in variant
    assert "safe-secret" not in json.dumps(report)
    assert "DON'T LEAK" not in json.dumps(report)


@pytest.mark.parametrize("runner", [run_amora_v2_v3, run_amora_v3_temperature])
async def test_transport_failed_on_one_variant_does_not_mark_other_core_rejected(
    runner, monkeypatch
):
    context_calls = []

    async def fake_post(self, payload, **kwargs):
        context_calls.append(payload)
        if len(context_calls) == 1:
            raise timeout_exception()
        return {
            "choices": [{"message": {"content": '{"moves":[],"closing":"statement"}'}}],
        }, {}, 200

    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, "_post_chat", fake_post)
    report = await runner(stub_core(), case="sacanagem")
    one, two = report["variants"]
    assert one["error_code"] == "EXPERIMENT_NVIDIA_TIMEOUT"
    assert one["validation_status"] == "not_run"
    assert two["validation_status"] == "rejected_by_core"
    assert two["error_code"] == "REALIZATION_SCHEMA_INVALID" or two["error_code"] == "EMPTY_REALIZATION"
    assert len(context_calls) == 2
