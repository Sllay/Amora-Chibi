"""C08: durable wakeups, remote empty polling, bounded offload and HTTP pooling."""
from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest

from amora.app import AmoraCore
from amora.cognition.numes_stream_diagnostics import NumesStreamDiagnostics
from amora.cognition.providers_numes import NumesCognitiveProvider
from amora.latency_trace import bind_latency_trace, install_remote_storage_probe
from amora.messaging import MessagingPlatform
from amora.messaging import reflection_queue as reflection_module
from amora.storage.remote import LibSQLBackend
from amora.time import SimulatedClock
from test_numes_groq_memory import configure, sse_reply
from test_reflection_provider_contract import request
from test_remote_storage import HranaSQLite


@pytest.mark.asyncio
async def test_c08_reflection_wakeup_and_recovery(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    q = core.messaging.reflections
    monkeypatch.setattr(reflection_module, "_IDLE_POLL_SECONDS", 1.0)
    monkeypatch.setattr(reflection_module, "_RECONCILE_SECONDS", 0.3)
    seen = []
    async def fake_reflect(job):
        seen.append(job[0])
        q.finish(job, "SYNTHETIC_DONE")
        return True
    monkeypatch.setattr(q, "_process_claimed", fake_reflect)
    async def until(predicate, timeout=2):
        async def wait():
            while not predicate():
                await asyncio.sleep(0.005)
        await asyncio.wait_for(wait(), timeout)
    try:
        first = request()
        core.cognition.observe_input(first)
        q.start()
        await asyncio.sleep(0.02)
        assert q.enqueue(first)
        await until(lambda: str(first.turn_id) in seen, timeout=0.75)
        assert q.state(first.turn_id)["status"] == "done"
        # No enqueue: the durable marker must still reconcile a crash gap.
        second = request()
        second.metadata["reflection_enqueue_requested"] = True
        core.cognition.observe_input(second)
        await until(lambda: str(second.turn_id) in seen)
        assert q.state(second.turn_id)["status"] == "done"
        assert seen.count(str(first.turn_id)) == 1
        assert seen.count(str(second.turn_id)) == 1
    finally:
        await q.stop()
        core.close()


@pytest.mark.asyncio
async def test_c08_remote_poll_offload_keeps_loop_alive_and_joins_shutdown(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    server = HranaSQLite(tmp_path / "remote.db")
    core = AmoraCore.bootstrap(clock=SimulatedClock(), storage=LibSQLBackend(
        "https://database.example", "synthetic-only", transport=httpx.MockTransport(server)))
    q = core.messaging.reflections
    started, release = threading.Event(), threading.Event()
    owner = threading.get_ident()
    threads = []
    original_poll = q._poll_owned
    try:
        seed = request()
        core.cognition.observe_input(seed)
        assert q.enqueue(seed)
        def blocked_poll(reconcile):
            threads.append(threading.get_ident())
            started.set()
            assert release.wait(5)
            return original_poll(reconcile)
        monkeypatch.setattr(q, "_poll_owned", blocked_poll)
        q.start()
        async def wait_started():
            while not started.is_set():
                await asyncio.sleep(0.005)
        await asyncio.wait_for(wait_started(), 2)
        # The worker is blocked while the asyncio loop remains responsive.
        assert len(threads) == 1 and threads[0] != owner
        task = asyncio.create_task(q.stop())
        await asyncio.sleep(0.02)
        assert not task.done(), "shutdown must join the in-flight DB poll"
        release.set()
        await asyncio.wait_for(task, 3)
        # A lease acquired just before cancellation is durably requeued.
        state = q.state(seed.turn_id)
        assert state["status"] == "pending"
        assert state["last_reason"] == "REFLECTION_SHUTDOWN"
    finally:
        release.set()
        await q.stop()
        core.close()


def test_c08_empty_remote_outbound_is_one_read(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    server = HranaSQLite(tmp_path / "remote.db")
    core = AmoraCore.bootstrap(clock=SimulatedClock(), storage=LibSQLBackend(
        "https://database.example", "synthetic-only", transport=httpx.MockTransport(server)))
    try:
        install_remote_storage_probe()
        with bind_latency_trace() as trace:
            assert core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD,
                                                        account_scope="primary") == ()
        assert trace["remote_sql_calls"] == 1
        assert trace["remote_sql_read_calls"] == 1
        assert trace.get("remote_sql_txn_calls", 0) == 0
    finally:
        core.close()


@pytest.mark.asyncio
async def test_c08_pool_lifecycle_and_transport_trace(monkeypatch):
    class Secrets:
        def get(self, name):
            return "synthetic-token"
    clients, requests = [], []
    original = httpx.AsyncClient
    async def handler(req):
        requests.append(req)
        callback = req.extensions["trace"]
        await callback("connection.connect_tcp.started", {"secret": "ignored"})
        await asyncio.sleep(0.001)
        await callback("connection.connect_tcp.complete", {})
        await callback("connection.start_tls.started", {})
        await callback("connection.start_tls.complete", {})
        return sse_reply({"answer": "4"})
    def factory(*args, **kwargs):
        client = original(*args, **kwargs, transport=httpx.MockTransport(handler))
        clients.append(client)
        return client
    monkeypatch.setattr(httpx, "AsyncClient", factory)
    provider = NumesCognitiveProvider(Secrets())
    await provider.start_transport()
    await provider.start_transport()
    try:
        with bind_latency_trace() as trace:
            assert (await provider.probe())["success"]
            assert (await provider.probe())["success"]
        assert len(clients) == 1 and len(requests) == 2
        assert not clients[0].is_closed
        assert trace["numes_diag_tcp_connect_attempts"] == 1
        assert trace["numes_diag_tls_attempts"] == 1
        assert trace["numes_diag_http_trace_events"] == 4
        assert trace["numes_diag_tcp_connect_ms"] >= 0
        assert "ignored" not in str(trace)
    finally:
        await provider.stop_transport()
    assert clients[0].is_closed
    await provider.stop_transport()
    assert (await provider.probe())["success"]
    assert len(clients) == 2 and clients[1].is_closed


@pytest.mark.asyncio
async def test_c08_remote_poll_owns_independent_connection_and_thread(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    server = HranaSQLite(tmp_path / "remote.db")
    core = AmoraCore.bootstrap(clock=SimulatedClock(), storage=LibSQLBackend(
        "https://database.example", "synthetic-only", transport=httpx.MockTransport(server)))
    q = core.messaging.reflections
    complete = threading.Event()
    observed = []
    original_poll = q._poll_owned
    owner = threading.get_ident()
    try:
        def probe(reconcile):
            result = original_poll(reconcile)
            worker = q._poll_worker
            observed.append((threading.get_ident(), worker.database, worker.database._conn))
            complete.set()
            return result
        monkeypatch.setattr(q, "_poll_owned", probe)
        async def wait_complete():
            while not complete.is_set():
                await asyncio.sleep(0.005)
        # Holding the primary lock must not block the remote worker's polling.
        # No transaction is held here; DB-level contention is a separate concern.
        with core.database._lock:
            q.start()
            await asyncio.wait_for(wait_complete(), 2)
        thread, db, conn = observed[0]
        assert thread != owner
        assert db is not core.database
        assert conn is not core.database._conn
        assert db._lock is not core.database._lock
        await q.stop()
        assert conn._closed
        assert q._poll_worker is None and q._poll_executor is None
        # Restart must not reuse a closed connection.
        complete.clear()
        q.start()
        await asyncio.wait_for(wait_complete(), 2)
        _, new_db, new_conn = observed[-1]
        assert new_db is not db and new_conn is not conn
        await q.stop()
        assert new_conn._closed
    finally:
        await q.stop()
        core.close()
