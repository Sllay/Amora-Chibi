"""Offline C02 benchmark. Real Core, identical fixtures, synthetic provider/Hrana transport."""
from __future__ import annotations

import json
import os
import statistics
import time

import httpx
import pytest

from amora.app import AmoraCore
from amora.messaging import ChannelType, RoutingAction
from amora.latency_trace import current_trace
from amora.storage.backend import SQLiteBackend
from amora.storage.remote import LibSQLBackend
from test_messaging_lifecycle_stability import CountingStructuredProvider, _clock
from test_platform_messaging_step2 import _bind_bk, _message
from test_remote_storage import HranaSQLite
from test_memory_v03 import important_candidate


def _percentiles(values):
    ordered = sorted(values)
    # Nearest-rank p95; with 6 observations this is the maximum.
    return {"p50": round(statistics.median(ordered), 3),
            "p95": round(ordered[-1], 3)}


@pytest.mark.asyncio
async def test_c02_core_storage_latency_baseline(tmp_path, monkeypatch, caplog, capsys):
    for key in tuple(os.environ):
        if key.startswith("AMORA_SECRET_") or key in {"AMORA_MODE", "AMORA_SOCIAL_PRIMARY"}:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    caplog.set_level("INFO", logger="amora.performance")
    results = []
    texts = (
        "Me conta uma coisa engraçada sobre programação.",
        "Minha amiga falou com a sua mãe e depois me contou o que ela disse. Quem falou com quem?",
        "O que você acha de criar um jogo com um ecossistema de animais autônomos?",
    )
    for name, delay in (("sqlite", 0), ("hrana_mock_0ms", 0), ("hrana_mock_5ms", 5)):
        folder = tmp_path / name
        folder.mkdir()
        monkeypatch.setenv("AMORA_HOME", str(folder / "runtime"))
        delay_enabled = False
        calls = 0
        scoped_calls = 0
        server = HranaSQLite(folder / "remote.db") if name != "sqlite" else None

        def transport(request):
            nonlocal calls, scoped_calls
            if delay_enabled:
                calls += 1
                if current_trace() is not None:
                    scoped_calls += 1
                if delay:
                    time.sleep(delay / 1000)
            return server(request)

        storage = (SQLiteBackend() if server is None else
                   LibSQLBackend("https://database.example", "synthetic-only",
                                 transport=httpx.MockTransport(transport)))
        core = AmoraCore.bootstrap(clock=_clock(), storage=storage)
        provider = CountingStructuredProvider(name="c02-offline")
        core.cognition.register_provider(provider)
        try:
            _bind_bk(core)
            assert core.memory.remember(important_candidate()).memory is not None
            # One warmup, identical history evolution in every backend.
            for index in range(7):
                delay_enabled = index > 0
                calls = 0
                scoped_calls = 0
                caplog.clear()
                incoming = _message(core, message_id=f"bench-{index}",
                                    actor_id="111111111111111111", channel_type=ChannelType.DM,
                                    conversation_id="c02-fixture-dm", text=texts[index % len(texts)])
                incoming = incoming.model_copy(update={"remote_processing_allowed": True})
                started = time.perf_counter()
                response = await core.messaging.receive(incoming)
                wall_ms = (time.perf_counter() - started) * 1000
                assert response.decision.action == RoutingAction.RESPOND
                assert len(response.outbound) == 1
                outbound = response.outbound[0]
                assert outbound.metadata["provider"] == "c02-offline"
                assert not outbound.metadata["fallback_used"]
                assert outbound.text == "Resposta remota estável."
                assert provider.calls == index + 1
                lines = [record.getMessage() for record in caplog.records
                         if record.name == "amora.performance"
                         and "kind=messaging " in record.getMessage()]
                assert len(lines) == 1
                trace = json.loads(lines[0].split("metrics=", 1)[1])
                assert trace["turn_total_ms"] > 0
                if index:
                    if server is not None:
                        assert trace["remote_sql_calls"] == scoped_calls > 0
                        assert calls >= scoped_calls
                    else:
                        assert trace.get("remote_sql_calls", 0) == 0
                    results.append({"backend": name, "turn": index, "wall_ms": round(wall_ms, 3),
                                    "transport_calls": calls, "unscoped_transport_calls": calls - scoped_calls,
                                    "trace": trace})
                delay_enabled = False
        finally:
            core.close()
        if server is not None:
            assert not server.streams

    summary = {}
    for name in ("sqlite", "hrana_mock_0ms", "hrana_mock_5ms"):
        rows = [row for row in results if row["backend"] == name]
        keys = ("turn_total_ms", "remote_sql_calls", "remote_sql_ms",
                "stage_context_compile_ms", "stage_validated_plan_ms",
                "stage_provider_attempt_ms", "event_loop_lag_max_ms")
        summary[name] = {key: _percentiles([row["trace"].get(key, 0) for row in rows])
                         for key in keys}
    # Expose numeric synthetic results in CI even under pytest capture.
    with capsys.disabled():
        print("C02_BENCHMARK_JSON=" + json.dumps({"summary": summary, "samples": results},
                                                sort_keys=True, separators=(",", ":")), flush=True)
    assert len(results) == 18
