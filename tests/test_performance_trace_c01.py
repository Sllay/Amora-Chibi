from __future__ import annotations

import asyncio
import json
import logging
import threading
from types import SimpleNamespace
from uuid import uuid4

import pytest

from amora.latency_trace import (
    bind_latency_trace, current_trace, install_remote_storage_probe, log_trace,
    span, trace_lock, trace_messaging_turn, trace_stage,
)
from amora.storage.remote import RemoteConnection


def test_remote_sql_attributed_to_innermost_stage(monkeypatch):
    calls = []

    def fake_pipeline(self, requests):
        calls.append(requests)
        return ["ok"]

    monkeypatch.setattr(RemoteConnection, "_pipeline", fake_pipeline)
    install_remote_storage_probe()
    install_remote_storage_probe()
    request = [{"type": "execute", "stmt": {"sql": "SELECT * FROM example WHERE x=?", "args": ["private"]}}]
    connection = object.__new__(RemoteConnection)
    with bind_latency_trace() as trace:
        with span("outer"):
            with span("inner"):
                assert connection._pipeline(request) == ["ok"]
                assert connection._pipeline(request) == ["ok"]
        assert trace["remote_sql_calls"] == 2
        assert trace["stage_inner_sql_calls"] == 2
        assert "stage_outer_sql_calls" not in trace
        assert trace["remote_sql_read_calls"] == 2
        assert trace["remote_sql_unique_shapes"] == 1
        assert trace["remote_sql_repeated_shapes"] == 1
        assert trace["stage_inner_calls"] == 1
    assert current_trace() is None
    assert len(calls) == 2


def test_stage_and_lock_preserve_exception_and_reset():
    @trace_stage("failure")
    def failing():
        with trace_lock(threading.RLock()):
            raise ValueError("private")

    with bind_latency_trace() as trace:
        with pytest.raises(ValueError, match="private"):
            failing()
        assert trace["stage_failure_calls"] == 1
        assert trace["lock_acquisitions"] == 1
        assert trace["lock_wait_ms"] >= 0
        assert trace["lock_held_ms"] >= 0
    assert current_trace() is None


@pytest.mark.asyncio
async def test_concurrent_owned_turns_isolated_and_private_scopes_skipped(caplog):
    class Service:
        @staticmethod
        def _turn_id(message):
            return message.turn_id

        @trace_messaging_turn
        async def run(self, message):
            with span("example"):
                await asyncio.sleep(0)
                if current_trace() is not None:
                    current_trace()["counter"] = message.counter
                    current_trace()["private_value"] = "SECRET_TEXT"
            return SimpleNamespace(outbound=[SimpleNamespace(id=message.outbound_id)])

    def message(counter, scope):
        return SimpleNamespace(counter=counter, turn_id=uuid4(), outbound_id=uuid4(),
                               privacy_scope=SimpleNamespace(value=scope))

    a, b, protected = message(1, "PERSONAL"), message(2, "PERSONAL"), message(3, "AUTH")
    with caplog.at_level(logging.INFO, logger="amora.performance"):
        await asyncio.gather(Service().run(a), Service().run(b), Service().run(protected))
    lines = [record.getMessage() for record in caplog.records if "performance_trace" in record.getMessage()]
    assert len(lines) == 2
    for item in (a, b):
        line = next(line for line in lines if str(item.turn_id) in line)
        assert str(item.outbound_id) in line
        data = json.loads(line.split("metrics=", 1)[1])
        assert data["counter"] == item.counter
        assert data["stage_example_calls"] == 1
        assert data["turn_total_ms"] >= 0
        assert "private_value" not in data
        assert "SECRET_TEXT" not in line
    assert all(str(protected.turn_id) not in line for line in lines)


def test_log_trace_never_emits_arbitrary_identifiers_or_text(caplog):
    with caplog.at_level(logging.INFO, logger="amora.performance"):
        log_trace("test", {"count": 1, "text": "PRIVATE", "_sql_shapes": {b"x"}},
                  turn_id="raw user name", outbound_id="raw channel id")
    assert "PRIVATE" not in caplog.text
    assert "raw user name" not in caplog.text
    assert "raw channel id" not in caplog.text
    assert '"count":1' in caplog.text
