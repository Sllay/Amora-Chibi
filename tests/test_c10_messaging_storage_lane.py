"""Remote DM storage ownership, durability, cancellation and invalidation."""
from __future__ import annotations

import asyncio
import json
import sqlite3
import threading

import pytest

from amora.messaging import RoutingAction
from amora.messaging.models import TransportInvalidation
from amora.messaging.store import MessagingStore
from test_c09_cognitive_storage_lane import remote_core, incoming
from test_messaging_lifecycle_stability import CountingStructuredProvider


async def wait_thread(event):
    async def wait():
        while not event.is_set():
            await asyncio.sleep(.005)
    await asyncio.wait_for(wait(), 3)


def invalidate(core, msg):
    return core.messaging.invalidate_transport(TransportInvalidation(
        platform=msg.platform, account_scope=msg.account_scope,
        conversation_id=msg.conversation_id, native_message_id=msg.native_source_message_id,
        reason="DISCORD_MESSAGE_UPDATED", transport_revision=2))


@pytest.mark.asyncio
async def test_c10_owned_lane_and_cancel_after_durable_claim(remote_core, monkeypatch):
    core = remote_core
    service = core.messaging
    service.start_storage_lane()
    lane = service.storage_lane
    entered, release = threading.Event(), threading.Event()
    owner = threading.get_ident()
    observed = []
    original = lane._execute
    def blocked(method, args, kwargs):
        value = original(method, args, kwargs)
        if method == "claim":
            observed.append((threading.get_ident(), lane.worker.database._conn))
            assert value is True
            entered.set()
            assert release.wait(5)
        return value
    monkeypatch.setattr(lane, "_execute", blocked)
    task = None
    stop = None
    try:
        msg = incoming(core, "c10-cancel-claim")
        with core.database._lock:
            task = asyncio.create_task(service.receive(msg))
            await wait_thread(entered)
            assert observed[0][0] != owner
            assert observed[0][1] is not core.database._conn
            assert lane.worker.database._lock is not core.database._lock
            stop = asyncio.create_task(service.stop_inflight())
            await asyncio.sleep(.02)
            assert not stop.done()
            release.set()
            await asyncio.wait_for(stop, 3)
        with pytest.raises(asyncio.CancelledError):
            await task
        # No stranded 180 s generation lease after cancellation.
        turn_id = str(service._turn_id(msg))
        assert service.generations.claim(turn_id, service._session_ref(msg),
            service._hash(msg.native_source_message_id), msg.transport_revision, "new-owner")
        assert service.generations.release(turn_id, "new-owner")
        conn = observed[0][1]
        await service.stop_storage_lane()
        assert conn._closed
    finally:
        release.set()
        if stop is not None:
            await asyncio.gather(stop, return_exceptions=True)
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await service.stop_storage_lane()


@pytest.mark.asyncio
async def test_c10_normal_dm_recovery_and_compact_trace(remote_core, caplog):
    core = remote_core
    service = core.messaging
    store = core.cognition.store
    provider = CountingStructuredProvider(name="c10-offline", delay=.01)
    core.cognition.register_provider(provider)
    store.start_async_lane()
    service.start_storage_lane()
    caplog.set_level("INFO", logger="amora.performance")
    try:
        msg = incoming(core, "c10-normal")
        result = await asyncio.wait_for(service.receive(msg), 10)
        assert result.decision.action == RoutingAction.RESPOND
        assert len(result.outbound) == 1
        outbound = result.outbound[0]
        assert outbound.text == "Resposta remota estável."
        assert service.store.get_outbound(outbound.id).text == outbound.text
        audit = service.store.get_audit(platform=msg.platform, account_scope=msg.account_scope,
                                       conversation_id=msg.conversation_id, message_id=msg.message_id)
        assert audit.responded
        retry = await service.receive(msg)
        assert retry.duplicate and retry.outbound[0].id == outbound.id
        assert provider.calls == 1
        rows = [json.loads(x.getMessage().split("metrics=", 1)[1])
                for x in caplog.records if x.name == "amora.performance"
                and "kind=critical_path " in x.getMessage()]
        assert rows[0]["stage_messaging_db_lane_calls"] >= 6
        assert rows[0]["stage_messaging_db_lane_sql_ms"] > 0
        assert rows[0]["stage_cognitive_db_lane_calls"] > 0
    finally:
        await service.stop_storage_lane()
        await store.stop_async_lane()


@pytest.mark.asyncio
async def test_c10_prepared_response_survives_worker_write_failure(remote_core, monkeypatch):
    core = remote_core
    service = core.messaging
    provider = CountingStructuredProvider(name="c10-offline", delay=0)
    core.cognition.register_provider(provider)
    service.start_storage_lane()
    original = MessagingStore.put_outbound
    failed = False
    def fail_once(self, outbound, **kwargs):
        nonlocal failed
        if self is not service.store and not failed:
            failed = True
            raise sqlite3.OperationalError("synthetic failure after durable stage")
        return original(self, outbound, **kwargs)
    monkeypatch.setattr(MessagingStore, "put_outbound", fail_once)
    try:
        msg = incoming(core, "c10-recovery")
        with pytest.raises(sqlite3.OperationalError, match="synthetic failure"):
            await service.receive(msg)
        assert failed and provider.calls == 1
        assert service.store.get_outbound(service._outbound_id(msg)) is None
        recovered = await service.receive(msg)
        assert recovered.duplicate
        assert len(recovered.outbound) == 1
        assert recovered.outbound[0].text == "Resposta remota estável."
        assert provider.calls == 1
    finally:
        await service.stop_storage_lane()


@pytest.mark.asyncio
@pytest.mark.parametrize("existing", [False, True])
async def test_c10_invalidation_during_audit_wait_blocks_stale_delivery(remote_core, monkeypatch, existing):
    core = remote_core
    service = core.messaging
    provider = CountingStructuredProvider(name="c10-offline", delay=0)
    core.cognition.register_provider(provider)
    service.start_storage_lane()
    lane = service.storage_lane
    entered, release = asyncio.Event(), asyncio.Event()
    task = None
    try:
        msg = incoming(core, "c10-invalidation")
        if existing:
            assert (await service.receive(msg)).outbound
        original = lane.call
        async def gated(method, *args, **kwargs):
            value = await original(method, *args, **kwargs)
            if method == ("get_audit" if existing else "append_audit"):
                entered.set()
                await release.wait()
            return value
        monkeypatch.setattr(lane, "call", gated)
        task = asyncio.create_task(service.receive(msg))
        await asyncio.wait_for(entered.wait(), 5)
        invalidate(core, msg)
        release.set()
        result = await asyncio.wait_for(task, 5)
        assert not result.outbound
        assert "REPLY_LEASE_INVALIDATED" in result.decision.reason_codes
        assert provider.calls == int(existing)
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await service.stop_storage_lane()
