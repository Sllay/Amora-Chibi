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
