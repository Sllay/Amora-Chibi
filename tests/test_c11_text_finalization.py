"""C11: preserve durable text history and voice contracts while batching remote SQL."""
from __future__ import annotations

import asyncio
import json
import sqlite3

import pytest

from amora.cognition import CognitiveRequest, JournalStage
from amora.events import PrivacyScope
from amora.latency_trace import bind_latency_trace, install_remote_storage_probe
from amora.messaging.models import TransportInvalidation
from test_c09_cognitive_storage_lane import remote_core, incoming
from test_messaging_lifecycle_stability import CountingStructuredProvider

TEXT = ("A primeira resposta explica o assunto com clareza. "
        "A segunda frase acrescenta um exemplo prático. "
        "A terceira frase mantém o contexto da conversa. "
        "A quarta frase termina a explicação normalmente. "
        "A quinta frase conserva toda a informação importante.")


class LongProvider(CountingStructuredProvider):
    async def realize(self, context):
        result = await super().realize(context)
        result["moves"][0]["text"] = TEXT
        return result


def request_for(core, *, text=TEXT):
    return CognitiveRequest(
        text="Explique o assunto.", speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
        session_id="c11-private", privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
        metadata={"surface": "platform_messaging", "channel_type": "dm",
                  "response_display_text": text, "test_mode": True,
                  "memory_consolidation": False, "relationship_learning": False,
                  "emotion_persistence": False},
    )


@pytest.mark.asyncio
async def test_c11_batched_text_matches_legacy_and_reduces_pipelines(remote_core):
    core = remote_core
    engine = core.cognition
    store = engine.store
    install_remote_storage_probe()
    from amora.cognition.speech import SpeechSegmenter
    segmenter = SpeechSegmenter()
    raw = [*segmenter.feed(TEXT), *segmenter.flush()]
    assert len(raw) == 5
    baseline, migrated = request_for(core), request_for(core)
    with bind_latency_trace() as old_trace:
        emitted = []
        for ordinal, part in enumerate(raw):
            emitted.append(await engine._emit_segment(baseline, part, ordinal, "fixture", "model", None))
    store.start_async_lane()
    try:
        with bind_latency_trace() as new_trace:
            result = await engine._persist_fresh_text(migrated, raw, "fixture", "model")
        assert result == emitted
        assert new_trace["remote_sql_calls"] < old_trace["remote_sql_calls"]
        assert old_trace["remote_sql_calls"] == 15
        assert new_trace["remote_sql_calls"] <= 8
        for req in (baseline, migrated):
            segments = store.list_segments(req.turn_id)
            assert [s.speech_text for s in segments] == emitted
            for ordinal, seg in enumerate(segments):
                row = store.get_message_by_dedupe(f"cognitive:{req.turn_id}:{ordinal}")
                assert row.raw_text == seg.display_text
                assert row.normalized_text == seg.speech_text
                assert row.metadata["segment_id"] == str(seg.id)
                assert seg.metadata["message_id"] == str(row.id)
                assert row.metadata["test_mode"] is True
                assert row.metadata["memory_consolidation"] is False
            journals = store.journal_for_turn(req.turn_id)
            assert len([j for j in journals if j.stage == JournalStage.SPEECH_SEGMENT_READY]) == 5
        # Existing history must not be overwritten or duplicated by the batch.
        assert await engine._persist_fresh_text(migrated, raw, "fixture", "model") is None
        assert len(store.list_segments(migrated.turn_id)) == 5
    finally:
        await store.stop_async_lane()


@pytest.mark.asyncio
async def test_c11_batch_rolls_back_on_second_table_failure(remote_core, monkeypatch):
    engine = remote_core.cognition
    store = engine.store
    request = request_for(remote_core)
    raw = ["Primeira frase completa.", "Segunda frase completa.", "Terceira frase completa."]
    store.start_async_lane()
    try:
        await store.async_lane.call("list_segments", request.turn_id)
        conn = store.async_lane.worker._conn
        original = conn.executemany
        def fail(sql, values):
            if "INSERT INTO conversation_messages" in sql:
                raise sqlite3.OperationalError("synthetic batch failure")
            return original(sql, values)
        monkeypatch.setattr(conn, "executemany", fail)
        with pytest.raises(sqlite3.OperationalError, match="synthetic batch failure"):
            await engine._persist_fresh_text(request, raw, "fixture", "model")
        assert store.list_segments(request.turn_id) == []
        assert store.list_messages(turn_id=request.turn_id) == []
        assert store.journal_for_turn(request.turn_id) == []
        monkeypatch.setattr(conn, "executemany", original)
        assert len(await engine._persist_fresh_text(request, raw, "fixture", "model")) == 3
    finally:
        await store.stop_async_lane()


@pytest.mark.asyncio
async def test_c11_end_to_end_text_and_single_observation(remote_core, caplog):
    core = remote_core
    store = core.cognition.store
    provider = LongProvider(name="c11-offline")
    core.cognition.register_provider(provider)
    store.start_async_lane()
    core.messaging.start_storage_lane()
    caplog.set_level("INFO", logger="amora.performance")
    try:
        msg = incoming(core, "c11-long")
        result = await asyncio.wait_for(core.messaging.receive(msg), 10)
        assert result.outbound[0].text == TEXT
        assert provider.calls == 1
        turn = core.messaging._turn_id(msg)
        journal = store.journal_for_turn(turn)
        assert len([j for j in journal if j.stage == JournalStage.INPUT_OBSERVED]) == 1
        assert len([j for j in journal if j.stage == JournalStage.RESPONSE_PLANNED]) == 2
        assert len(store.list_segments(turn)) == 5
        assert sum(m.speaker_id == "PERSON_BK" for m in store.list_messages(turn_id=turn)) == 1
        logs = [x.getMessage() for x in caplog.records if x.name == "amora.performance"]
        prepare = json.loads(next(x for x in logs if "kind=critical_prepare " in x).split("metrics=", 1)[1])
        finalize = json.loads(next(x for x in logs if "kind=critical_finalize " in x).split("metrics=", 1)[1])
        assert prepare["stage_record_input_calls"] == 1
        assert finalize["stage_text_batch_persist_sql_calls"] <= 8
        assert finalize["stage_text_final_journals_sql_calls"] == 1
        retry = await core.messaging.receive(msg)
        assert retry.duplicate and retry.outbound[0].id == result.outbound[0].id
        assert provider.calls == 1
    finally:
        await core.messaging.stop_storage_lane()
        await store.stop_async_lane()


@pytest.mark.asyncio
async def test_c11_invalidated_source_cannot_commit_batch(remote_core, monkeypatch):
    core = remote_core
    store = core.cognition.store
    provider = LongProvider(name="c11-invalidation")
    core.cognition.register_provider(provider)
    store.start_async_lane()
    lane = store.async_lane
    entered, release = asyncio.Event(), asyncio.Event()
    original = lane.call
    async def gated(method, *args, **kwargs):
        if method == "persist_text_batch":
            entered.set()
            await release.wait()
        return await original(method, *args, **kwargs)
    monkeypatch.setattr(lane, "call", gated)
    task = None
    try:
        msg = incoming(core, "c11-invalidated")
        task = asyncio.create_task(core.messaging.receive(msg))
        await asyncio.wait_for(entered.wait(), 10)
        core.messaging.invalidate_transport(TransportInvalidation(
            platform=msg.platform, account_scope=msg.account_scope,
            conversation_id=msg.conversation_id, native_message_id=msg.native_source_message_id,
            reason="DISCORD_MESSAGE_UPDATED", transport_revision=2))
        release.set()
        result = await asyncio.wait_for(task, 10)
        assert not result.outbound
        assert not store.list_segments(core.messaging._turn_id(msg))
    finally:
        release.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        await store.stop_async_lane()
