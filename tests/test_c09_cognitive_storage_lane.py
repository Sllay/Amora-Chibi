"""C09: real Core with synthetic remote storage and no provider network."""
from __future__ import annotations

import asyncio
import json
import os
import threading
import time

import httpx
import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest, JournalStage
from amora.events import PrivacyScope
from amora.messaging import ChannelType, RoutingAction
from amora.messaging.models import TransportInvalidation
from amora.storage.remote import LibSQLBackend
from test_messaging_lifecycle_stability import CountingStructuredProvider, _clock
from test_platform_messaging_step2 import _bind_bk, _message
from test_remote_storage import HranaSQLite


@pytest.fixture
def remote_core(tmp_path, monkeypatch):
    for key in tuple(os.environ):
        if key.startswith("AMORA_SECRET_") or key in {"AMORA_MODE", "AMORA_SOCIAL_PRIMARY"}:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    server = HranaSQLite(tmp_path / "remote.db")
    core = AmoraCore.bootstrap(clock=_clock(), storage=LibSQLBackend(
        "https://database.example", "synthetic-only", transport=httpx.MockTransport(server)))
    try:
        _bind_bk(core)
        yield core
    finally:
        core.close()
        assert not server.streams


def incoming(core, name):
    return _message(core, message_id=name, actor_id="111111111111111111",
                    channel_type=ChannelType.DM, conversation_id="c09-private",
                    text="Me conta uma coisa engraçada sobre programação.").model_copy(
                        update={"remote_processing_allowed": True})


@pytest.mark.asyncio
async def test_c09_lane_is_independent_and_joins_cancelled_operation(remote_core, monkeypatch):
    store = remote_core.cognition.store
    store.start_async_lane()
    lane = store.async_lane
    entered, release = threading.Event(), threading.Event()
    original = lane._execute
    owner = threading.get_ident()
    threads = []
    def blocked(method, args, kwargs):
        threads.append(threading.get_ident())
        entered.set()
        assert release.wait(5)
        return original(method, args, kwargs)
    monkeypatch.setattr(lane, "_execute", blocked)
    task = None
    try:
        async def wait_entered():
            while not entered.is_set():
                await asyncio.sleep(.005)
        # Holding the primary store lock does not prevent the worker reading
        # through its independently owned connection.
        with store._lock:
            task = asyncio.create_task(lane.call("list_messages", limit=1))
            await asyncio.wait_for(wait_entered(), 2)
            task.cancel()
            await asyncio.sleep(.02)
            assert not task.done()
            release.set()
            with pytest.raises(asyncio.CancelledError):
                await asyncio.wait_for(task, 2)
        assert threads == [threads[0]] and threads[0] != owner
        assert lane.worker._conn is not store._conn
        assert lane.worker._lock is not store._lock
        conn = lane.worker._conn
        with pytest.raises(ValueError):
            await lane.call("close")
        await store.stop_async_lane()
        assert conn._closed and store.async_lane is None
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await store.stop_async_lane()


@pytest.mark.asyncio
async def test_c09_compiler_preserves_full_context_and_history(remote_core):
    core = remote_core
    store = core.cognition.store
    req = CognitiveRequest(text="Minha amiga falou com a sua mãe. Quem falou com quem?",
                           speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
                           session_id="c09-snapshot", privacy_scope=PrivacyScope.PERSONAL,
                           remote_processing_allowed=True,
                           metadata={"surface": "platform_messaging", "channel_type": "dm"})
    # A fixed clock makes the two deterministic snapshots comparable.
    core.cognition.observe_input(req)
    expected = core.cognition.compiler.compile(req)
    store.start_async_lane()
    try:
        actual = await core.cognition.compiler.compile_async(req)
        assert actual.model_dump(mode="json") == expected.model_dump(mode="json")
        assert store.async_lane.worker is not None
    finally:
        await store.stop_async_lane()


@pytest.mark.asyncio
async def test_c09_messaging_persists_replay_and_deduplicates(remote_core, caplog):
    core = remote_core
    store = core.cognition.store
    provider = CountingStructuredProvider(name="c09-offline", delay=.01)
    core.cognition.register_provider(provider)
    store.start_async_lane()
    caplog.set_level("INFO", logger="amora.performance")
    try:
        msg = incoming(core, "c09-live")
        result = await asyncio.wait_for(core.messaging.receive(msg), 10)
        assert result.decision.action == RoutingAction.RESPOND
        assert len(result.outbound) == 1
        assert result.outbound[0].text == "Resposta remota estável."
        assert result.outbound[0].metadata["provider"] == "c09-offline"
        turn_id = core.messaging._turn_id(msg)
        journal = store.journal_for_turn(turn_id)
        assert any("REPLAY_SNAPSHOT" in x.reason_codes for x in journal)
        assert any(x.stage == JournalStage.MODEL_STARTED for x in journal)
        assert len([x for x in store.list_messages(turn_id=turn_id) if x.speaker_id == "PERSON_BK"]) == 1
        retry = await core.messaging.receive(msg)
        assert retry.duplicate and retry.outbound[0].id == result.outbound[0].id
        assert provider.calls == 1
        rows = [json.loads(x.getMessage().split("metrics=", 1)[1])
                for x in caplog.records if x.name == "amora.performance"
                and "kind=messaging " in x.getMessage()]
        assert any(row.get("stage_cognitive_db_lane_calls", 0) > 0 for row in rows)
    finally:
        await store.stop_async_lane()


@pytest.mark.asyncio
async def test_c09_invalidation_during_preparation_never_calls_provider(remote_core, monkeypatch):
    core = remote_core
    store = core.cognition.store
    provider = CountingStructuredProvider(name="c09-offline", delay=0)
    core.cognition.register_provider(provider)
    store.start_async_lane()
    lane = store.async_lane
    original = lane.call
    entered, release = asyncio.Event(), asyncio.Event()
    async def gated(method, *args, **kwargs):
        value = await original(method, *args, **kwargs)
        if method == "put_journal" and args[0].stage == JournalStage.MODEL_STARTED:
            entered.set()
            await release.wait()
        return value
    monkeypatch.setattr(lane, "call", gated)
    task = None
    try:
        msg = incoming(core, "c09-invalidate")
        task = asyncio.create_task(core.messaging.receive(msg))
        await asyncio.wait_for(entered.wait(), 10)
        core.messaging.invalidate_transport(TransportInvalidation(
            platform=msg.platform, account_scope=msg.account_scope,
            conversation_id=msg.conversation_id, native_message_id=msg.native_source_message_id,
            reason="DISCORD_MESSAGE_UPDATED", transport_revision=2))
        release.set()
        result = await asyncio.wait_for(task, 10)
        assert not result.outbound
        assert "REPLY_LEASE_INVALIDATED" in result.decision.reason_codes
        assert provider.calls == 0
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await store.stop_async_lane()
