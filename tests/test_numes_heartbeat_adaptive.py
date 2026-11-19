"""Bounded Numes SSE liveness, timeout and usage regression tests (offline)."""
from __future__ import annotations

import asyncio
import json
import logging

import httpx
import pytest

from amora.cognition.numes_stream_diagnostics import (
    ACTIVE_NUMES_LIVENESS, NumesConnectionLiveness, NumesStreamDiagnostics,
)
from amora.cognition.providers_numes import NumesCognitiveProvider
from amora.cognition.structured_runtime import _await_numes_adaptive
from test_reflection_provider_contract import fake_http


def test_connection_liveness_is_not_inference_progress():
    now = [0.0]
    watch = NumesConnectionLiveness(clock=lambda: now[0])
    watch.headers(200)
    assert not watch.healthy()
    watch.keepalive()
    assert watch.keepalives == 1 and watch.progress_deltas == 0
    assert watch.healthy()
    now[0] = 24.0
    assert not watch.healthy()
    watch.progress()
    assert watch.healthy() and watch.progress_deltas == 1
    watch.headers(503)
    assert not watch.healthy()


@pytest.mark.asyncio
async def test_healthy_keepalive_gets_one_bounded_grace(caplog):
    async def provider():
        watch = ACTIVE_NUMES_LIVENESS.get()
        assert watch is not None
        watch.headers(200)
        watch.keepalive()
        await asyncio.sleep(0.065)
        return {"answer": 4}

    with caplog.at_level(logging.INFO, logger="amora.cognition.numes"):
        result = await _await_numes_adaptive(
            provider(), timeout=0.02, available_seconds=0.12,
            reserve_seconds=0.005, grace_cap_seconds=0.08,
        )
    assert result["answer"] == 4
    assert any(r.message == "numes_adaptive_grace" for r in caplog.records)
    assert any(getattr(r, "reason", None) == "keepalive_only" for r in caplog.records)


@pytest.mark.asyncio
async def test_headers_without_heartbeat_never_extend():
    cancelled = []
    async def provider():
        watch = ACTIVE_NUMES_LIVENESS.get()
        watch.headers(200)
        try:
            await asyncio.sleep(0.20)
        finally:
            cancelled.append(True)

    with pytest.raises(asyncio.TimeoutError):
        await _await_numes_adaptive(
            provider(), timeout=0.02, available_seconds=0.12,
            reserve_seconds=0.005, grace_cap_seconds=0.08,
        )
    assert cancelled == [True]


@pytest.mark.asyncio
async def test_keepalive_cannot_keep_request_alive_indefinitely():
    cancelled = []
    async def provider():
        watch = ACTIVE_NUMES_LIVENESS.get()
        watch.headers(200)
        watch.keepalive()
        try:
            await asyncio.sleep(1)
        finally:
            cancelled.append(True)

    with pytest.raises(asyncio.TimeoutError):
        await _await_numes_adaptive(
            provider(), timeout=0.02, available_seconds=0.06,
            reserve_seconds=0.005, grace_cap_seconds=0.03,
        )
    assert cancelled == [True]


@pytest.mark.asyncio
async def test_stale_heartbeat_and_no_total_budget_never_extend():
    clock = [0.0]
    watch = NumesConnectionLiveness(clock=lambda: clock[0])
    watch.headers(200)
    watch.keepalive()
    clock[0] = 25.0
    assert not watch.healthy()


@pytest.mark.asyncio
async def test_numes_sse_keepalives_and_actual_usage_without_private_log(monkeypatch, caplog):
    first = {"choices": [{"index": 0, "delta": {"reasoning": "PRIVATE_REASONING"}}]}
    reply = {"choices": [{"index": 0, "delta": {"content": '{"answer": "4"}'}}]}
    stop = {"choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]}
    usage = {"choices": [], "usage": {
        "prompt_tokens": 100, "completion_tokens": 24, "total_tokens": 124,
        "token_count_basis": "provider_native", "cost": 0.0,
        "prompt_tokens_details": {"cached_tokens": 80},
        "completion_tokens_details": {"reasoning_tokens": 9},
    }}
    raw = ": keepalive\n\n: keepalive\n\n" + "".join(
        "data: " + json.dumps(chunk) + "\n\n"
        for chunk in (first, reply, stop, usage)
    ) + "data: [DONE]\n\n"

    def handler(_):
        return httpx.Response(200, content=raw.encode("utf-8"),
                              headers={"content-type": "text/event-stream"})
    fake_http(monkeypatch, handler)
    class Secrets:
        def get(self, _):
            return "SYNTHETIC_SECRET"
    provider = NumesCognitiveProvider(Secrets())
    with caplog.at_level(logging.INFO, logger="amora.cognition.numes"):
        response = await provider._complete_stream({}, key="SYNTHETIC_SECRET",
                                                   phase="synthetic_probe")
    assert response["answer"] == "4"
    assert response["_usage"]["input_tokens"] == 100
    assert response["_usage"]["output_tokens"] == 24
    assert response["_usage"]["cached_tokens"] == 80
    assert response["_usage"]["reasoning_tokens"] == 9
    assert response["_usage"]["cost"] == 0.0
    log = next(r for r in caplog.records if r.message == "numes_stream_diagnostics")
    assert log.keepalive_count == 2
    assert log.sse_data_events == 5
    assert log.total_tokens_reported == 124
    assert log.cached_tokens_reported == 80
    assert log.reasoning_tokens_reported == 9
    assert log.token_count_basis == "provider_native"
    assert log.cost_reported == 0.0
    assert "PRIVATE_REASONING" not in str([r.__dict__ for r in caplog.records])
    assert "SYNTHETIC_SECRET" not in str([r.__dict__ for r in caplog.records])


def test_missing_usage_is_unknown_not_zero():
    result = NumesStreamDiagnostics().close()
    assert "prompt_tokens_reported" not in result
    assert "completion_tokens_reported" not in result
    assert "cost_reported" not in result
