"""Offline C08: observed SSE timing/volume without recording private text."""
import asyncio
import json
import logging
import time

import httpx
import pytest

from amora.app import AmoraCore
from amora.cognition.numes_stream_diagnostics import NumesStreamDiagnostics
from amora.cognition.providers_numes import NumesCognitiveProvider
from amora.cognition.providers import ProviderError
from amora.latency_trace import bind_latency_trace
from amora.time import SimulatedClock
from test_numes_groq_memory import configure
from test_reflection_provider_contract import fake_http


def test_diagnostic_phases_and_usage_are_distinct():
    now = [0.0]
    d = NumesStreamDiagnostics(clock=lambda: now[0])
    now[0] = 1
    d.data_event()
    d.reasoning({"reasoning": "abc", "reasoning_content": "abc"})
    now[0] = 3
    d.data_event()
    d.content("hello")
    now[0] = 5
    d.data_event()
    d.content(" world")
    now[0] = 6
    d.data_event()
    d.finish()
    now[0] = 10
    d.data_event(done=True)
    m = d.close(usage={"completion_tokens": 20, "completion_tokens_details": {
        "reasoning_tokens": 12}}, parsed={"moves": [{"text": "hello world"}]})
    assert m["reasoning_chars"] == 3
    assert m["reasoning_deltas"] == 1
    assert m["content_deltas"] == 2 and m["content_chars"] == 11
    assert m["surface_chars"] == 11
    assert m["last_reasoning_ms"] == 1000
    assert m["content_span_ms"] == 2000
    assert m["content_max_gap_ms"] == 2000
    assert m["last_content_to_done_ms"] == 5000
    assert m["sse_max_gap_ms"] == 4000
    assert m["finish_reason_ms"] == 6000
    assert m["sse_data_events"] == 5
    assert m["completion_tokens_reported"] == 20
    assert m["reasoning_tokens_reported"] == 12
    assert "hello" not in json.dumps(m)


def test_optional_reasoning_fields_and_unknown_token_counts():
    d = NumesStreamDiagnostics()
    d.reasoning({"reasoning_details": [{"text": "abc"},
                {"summary": "de"}, {"data": "OPAQUE_SECRET"}]})
    m = d.close(usage={"completion_tokens": True, "reasoning_tokens": "12"})
    assert m["reasoning_chars"] == 5
    assert "completion_tokens_reported" not in m
    assert "reasoning_tokens_reported" not in m
    assert "OPAQUE_SECRET" not in json.dumps(m)


@pytest.mark.asyncio
async def test_sampler_detects_local_block_and_is_cancelled():
    d = NumesStreamDiagnostics()
    d.start_sampler()
    # A deliberately blocked local event loop, not a slow remote server.
    time.sleep(0.24)
    await asyncio.sleep(0.01)
    d.content("x")
    m = d.close()
    assert m["loop_samples"] >= 1
    assert m["loop_lag_max_ms"] >= 100
    assert m["loop_lag_before_content_max_ms"] >= 100
    assert m["loop_lag_over_100ms"] >= 1
    await asyncio.sleep(0.12)
    assert d.metrics["loop_samples"] == m["loop_samples"]


@pytest.mark.asyncio
@pytest.mark.parametrize("done", [True, False])
async def test_numes_emits_compact_diagnostics_on_success_and_failure(tmp_path, monkeypatch, caplog, done):
    configure(monkeypatch, tmp_path)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        raw_text = json.dumps({"moves": [{"kind": "answer", "text": "PRIVATE_SURFACE"}]})
        chunks = [
            {"choices": [{"delta": {"reasoning_content": "PRIVATE_REASONING",
                                      "reasoning": "PRIVATE_REASONING"}}]},
            {"choices": [{"delta": {"content": raw_text[:10]}}]},
            {"choices": [{"delta": {"content": raw_text[10:]}, "finish_reason": "stop"}]},
            {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 25,
             "completion_tokens_details": {"reasoning_tokens": 15}}},
        ]
        body = "".join("data: " + json.dumps(c) + "\n\n" for c in chunks)
        if done:
            body += "data: [DONE]\n\n"
        fake_http(monkeypatch, lambda req: httpx.Response(
            200, content=body.encode(), headers={"content-type": "text/event-stream"}))
        provider = NumesCognitiveProvider(core.secrets)
        with caplog.at_level(logging.INFO, logger="amora.cognition.numes"), bind_latency_trace() as trace:
            if done:
                result = await provider._complete_stream({}, key="SYNTHETIC_SECRET", phase="realization")
                assert result["moves"][0]["text"] == "PRIVATE_SURFACE"
                assert result["_usage"]["output_tokens"] == 25
            else:
                with pytest.raises(ProviderError):
                    await provider._complete_stream({}, key="SYNTHETIC_SECRET", phase="realization")
        logs = [r for r in caplog.records if r.name == "amora.cognition.numes"]
        rows = [r for r in logs if r.getMessage() == "numes_stream_diagnostics"]
        assert len(rows) == 1
        row = rows[0]
        assert row.outcome == ("completed" if done else "failed")
        assert row.reasoning_chars == len("PRIVATE_REASONING")
        assert row.content_chars == len(raw_text)
        assert row.reasoning_tokens_reported == 15
        assert row.completion_tokens_reported == 25
        assert row.content_deltas == 2
        assert row.sse_data_events == (5 if done else 4)
        assert trace["numes_diag_content_chars"] == len(raw_text)
        assert trace["numes_diag_reasoning_tokens_reported"] == 15
        assert hasattr(row, "surface_chars") is done
        assert hasattr(row, "done_ms") is done
        if done:
            assert row.surface_chars == len("PRIVATE_SURFACE")
        serialized = str([r.__dict__ for r in logs])
        for private in ("PRIVATE_SURFACE", "PRIVATE_REASONING", "SYNTHETIC_SECRET"):
            assert private not in serialized
    finally:
        core.close()
