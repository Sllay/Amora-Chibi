from datetime import timedelta

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.cognition.proposals import CognitiveProposalBundle
from amora.events import PrivacyScope
from amora.time import SimulatedClock
from test_reflection_v10 import StructuredProvider


def _request():
    return CognitiveRequest(
        text="Estou trabalhando em um jogo novo.",
        speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
        session_id="synthetic-private-session",
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
        metadata={"surface": "platform_messaging", "channel_type": "dm",
                  "memory_consolidation": True},
    )


@pytest.mark.asyncio
async def test_reflection_queue_survives_restart_and_expired_lease(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = SimulatedClock()
    request = _request()
    first = AmoraCore.bootstrap(clock=clock)
    try:
        first.cognition.observe_input(request)
        queue = first.messaging.reflections
        assert queue.enqueue(request)
        assert not queue.enqueue(request)
        lease = queue.claim()
        assert lease is not None
        assert queue.claim() is None
    finally:
        first.close()

    clock.advance(timedelta(seconds=181))
    second = AmoraCore.bootstrap(clock=clock)
    try:
        second.cognition.register_provider(StructuredProvider())
        queue = second.messaging.reflections
        assert await queue.process_once()
        assert queue.state(request.turn_id) == {
            "status": "done", "attempts": 2, "last_reason": "MEMORY_REFLECTION_ACCEPTED",
        }
        assert not await queue.process_once()
        assert not queue.finish(lease, "STALE_ACK")
        assert second.memory.store.count() >= 1
    finally:
        second.close()


@pytest.mark.asyncio
async def test_failed_reflection_retries_with_backoff_and_preserves_input(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = SimulatedClock()
    core = AmoraCore.bootstrap(clock=clock)
    request = _request()
    try:
        core.cognition.observe_input(request)
        queue = core.messaging.reflections
        assert queue.enqueue(request)
        calls = []

        async def unavailable(req):
            calls.append(req)
            return CognitiveProposalBundle(reason_codes=["REFLECTION_PROVIDER_FAILURE"])

        monkeypatch.setattr(core.cognition, "reflect", unavailable)
        assert await queue.process_once()
        assert queue.state(request.turn_id)["status"] == "pending"
        assert not await queue.process_once()
        clock.advance(timedelta(seconds=15))
        assert await queue.process_once()
        assert len(calls) == 2
        assert all(call.text == request.text and call.session_id == request.session_id
                   and call.turn_id == request.turn_id and call.source_event_id is None
                   for call in calls)
        assert queue.state(request.turn_id)["attempts"] == 2
    finally:
        core.close()


def test_reflection_queue_does_not_enqueue_test_or_protected_inputs(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        request = _request()
        for change in (
            {"remote_processing_allowed": False},
            {"privacy_scope": PrivacyScope.SECRET},
            {"metadata": {**request.metadata, "test_mode": True}},
            {"metadata": {**request.metadata, "memory_consolidation": False}},
        ):
            blocked = request.model_copy(update=change)
            assert not core.messaging.reflections.enqueue(blocked)
        assert core.messaging.reflections.claim() is None
    finally:
        core.close()


def _revision(number=1):
    return _request().model_copy(update={"metadata": {
        **_request().metadata, "native_message_id_hash": "synthetic-native-hash",
        "transport_revision": number,
    }})


@pytest.mark.asyncio
async def test_edit_cancels_inflight_reflection_and_new_revision_is_eligible(tmp_path, monkeypatch):
    import asyncio
    from amora.memory import MemoryQuery

    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    entered, release = asyncio.Event(), asyncio.Event()

    class DelayedProvider(StructuredProvider):
        async def propose(self, context):
            entered.set()
            await release.wait()
            return await super().propose(context)

    old = _revision()
    new = _revision(2)
    try:
        core.cognition.register_provider(DelayedProvider())
        queue = core.messaging.reflections
        core.cognition.observe_input(old)
        assert queue.enqueue(old)
        running = asyncio.create_task(queue.process_once())
        await asyncio.wait_for(entered.wait(), 2)
        assert queue.invalidate_source(old.session_id, "synthetic-native-hash", through_revision=1) == 1
        release.set()
        await asyncio.wait_for(running, 2)
        assert queue.state(old.turn_id)["status"] == "cancelled"
        assert not queue.retry_failed(old.turn_id)
        assert not core.memory.recall(MemoryQuery(text="jogo novo"))
        assert core.cognition.store.list_messages(session_id=old.session_id, exclude_invalidated=True) == []
        core.cognition.observe_input(new)
        assert queue.enqueue(new)
        assert await queue.process_once()
        assert queue.state(new.turn_id)["status"] == "done"
        hits = core.memory.recall(MemoryQuery(text="jogo novo"))
        assert hits and all(hit.memory.metadata["turn_id"] == str(new.turn_id) for hit in hits)
        assert queue.invalidate_source(new.session_id, "synthetic-native-hash", through_revision=1_000_000) == 1
        assert not core.memory.recall(MemoryQuery(text="jogo novo"))
        assert core.memory.get(hits[0].memory.id).archived_at is not None
    finally:
        release.set()
        core.close()


def test_invalidation_before_delivery_and_operator_retry(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        queue = core.messaging.reflections
        old = _revision()
        queue.invalidate_source(old.session_id, "synthetic-native-hash", through_revision=1)
        core.cognition.observe_input(old)
        assert not queue.enqueue(old)
        new = _revision(2)
        core.cognition.observe_input(new)
        assert queue.enqueue(new)
        for i in range(5):
            job = queue.claim()
            assert job and queue.finish(job, "REFLECTION_PROVIDER_FAILURE", retry=True)
            core.clock.advance(timedelta(seconds=1000))
        assert queue.state(new.turn_id)["status"] == "failed"
        assert queue.failed_jobs()[0]["turn_id"] == str(new.turn_id)
        assert queue.retry_failed(new.turn_id)
        assert queue.state(new.turn_id)["attempts"] == 0
        assert not queue.retry_failed(new.turn_id)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_historical_recovery_and_reviewed_reuse_preserve_audience(tmp_path, monkeypatch):
    from amora.memory import MemoryQuery
    from amora.memory.models import MemoryProvenance, ProvenanceKind
    from test_memory_v03 import important_candidate

    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.delenv("AMORA_REFLECTION_REMOTE_REUSE", raising=False)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        queue = core.messaging.reflections
        request = _revision()
        core.cognition.observe_input(request)
        assert str(request.turn_id) in queue.historical_audit()["unconsolidated_turn_candidates"]
        assert queue.recover_historical_turn(request.turn_id)
        assert not queue.recover_historical_turn(request.turn_id)
        core.cognition.register_provider(StructuredProvider())
        assert await queue.process_once()
        hits = core.memory.recall(MemoryQuery(text="jogo novo"))
        assert hits
        memory = hits[0].memory
        assert not memory.remote_processing_allowed
        assert queue.historical_audit()["local_only_reflections"]["total"] >= 1
        assert not core.memory.store.authorize_reviewed_reflection(memory.id, "ENTITY_CHATGPT")
        assert core.memory.store.authorize_reviewed_reflection(memory.id, "PERSON_BK")
        assert core.memory.get(memory.id).remote_processing_allowed
        query = MemoryQuery(text="jogo novo", projection_recipient_id="PERSON_BK", projection_remote_only=True)
        assert any(hit.memory.id == memory.id for hit in core.memory.recall(query))
        assert not core.memory.recall(query.model_copy(update={"projection_public_only": True}))
        assert not core.memory.recall(query.model_copy(update={"projection_recipient_id": "ENTITY_CHATGPT"}))
        # Source-less historical inferences cannot be promoted.
        orphan = core.memory.remember(important_candidate(
            content="Registro sem fonte comprovada.",
            provenance=MemoryProvenance(kind=ProvenanceKind.INFERENCE, source="cognition:test",
                                        actor_id="ENTITY_AMORA", confidence=0.95),
            remote_processing_allowed=False, owner_entity_id="PERSON_BK",
        )).memory
        assert orphan and not core.memory.store.authorize_reviewed_reflection(orphan.id, "PERSON_BK")
    finally:
        core.close()
