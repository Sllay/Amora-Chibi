from __future__ import annotations

import asyncio

import pytest

from amora.app import AmoraCore
from amora.messaging import ChannelType, ExternalActorRef, InboundMessage, MessagingPlatform, RoutingAction
from amora.time import SimulatedClock


def _direct_group_message(core: AmoraCore, *, message_id: str = "budget-1") -> InboundMessage:
    return InboundMessage(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        conversation_id="discord-budget-channel",
        message_id=message_id,
        channel_type=ChannelType.GROUP,
        sender_identifiers=[
            ExternalActorRef(
                identifier_type="discord_user_id",
                external_actor_id="111111111111111111",
                confidence=1.0,
            )
        ],
        text="Amora, me responde",
        mentions_amora=True,
        timestamp=core.clock.now(),
        remote_processing_allowed=True,
    )


def test_local_fallback_matches_greetings_as_words_not_substrings(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        composer = core.conversation.composer
        assert composer.fallback_dialogue("Ei, Amora") == "Oi, papis. Tô aqui."
        assert composer.fallback_dialogue("Sei mas é serio amora vc ta recebendo tudo dboa") == "Uhum."
        assert composer.fallback_dialogue("oi namorada") == "Uhum."
    finally:
        core.close()


@pytest.mark.asyncio
async def test_messaging_cognition_hard_timeout_queues_operational_fallback_instead_of_silence(
    tmp_path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    cancelled = asyncio.Event()

    async def no_appraise(*args, **kwargs) -> None:
        return None

    async def slow_respond(*args, **kwargs):
        try:
            await asyncio.sleep(30)
        finally:
            cancelled.set()

    try:
        monkeypatch.setattr(core.conversation, "_appraise_interaction", no_appraise)
        monkeypatch.setattr(core.cognition, "respond", slow_respond)
        core.messaging.cognition_timeout_seconds = 0.01
        core.messaging.provider_total_budget_seconds = 0.005

        result = await core.messaging.receive(_direct_group_message(core))

        assert cancelled.is_set()
        assert result.decision.action == RoutingAction.RESPOND
        assert len(result.outbound) == 1
        outbound = result.outbound[0]
        assert outbound.text == core.conversation.composer.operational_timeout_fallback()
        assert "[AMORA:E_TIMEOUT]" in outbound.text
        assert outbound.metadata["fallback_used"] is True
        assert outbound.metadata["provider"] is None
        assert outbound.metadata["remote_processing_allowed"] is True
        assert "MESSAGING_COGNITION_HARD_TIMEOUT" in outbound.metadata["cognitive_reason_codes"]
        assert "OPERATIONAL_TIMEOUT_FALLBACK" in outbound.metadata["cognitive_reason_codes"]
    finally:
        core.close()


def test_discord_sixty_second_generation_deadline_ladder_is_bounded() -> None:
    """A social provider may take ~60s without another layer cancelling it first."""
    from amora.messaging.service import (
        _MESSAGING_COGNITION_TIMEOUT_SECONDS,
        _MESSAGING_PROVIDER_ATTEMPT_BUDGET_SECONDS,
        _MESSAGING_PROVIDER_TOTAL_BUDGET_SECONDS,
    )
    from amora.security import http_processing_deadline_seconds

    attempt = _MESSAGING_PROVIDER_ATTEMPT_BUDGET_SECONDS
    total = _MESSAGING_PROVIDER_TOTAL_BUDGET_SECONDS
    cognition = _MESSAGING_COGNITION_TIMEOUT_SECONDS
    inbound_http = http_processing_deadline_seconds(
        "/v1/messaging/worker/inbound", "POST", worker=True
    )
    assert 60 < attempt < total < cognition < 120 < inbound_http
    assert total - attempt >= 10
    assert cognition - total >= 10
    # Only the messaging inbound endpoint receives an extended HTTP deadline.
    assert http_processing_deadline_seconds("/v1/web/utterance", "POST", worker=False) == 90
    assert http_processing_deadline_seconds("/v1/web/utterance/stream", "POST", worker=False) == 270
    assert http_processing_deadline_seconds("/v1/messaging/worker/outbound", "GET", worker=True) == 90
    assert http_processing_deadline_seconds("/v1/messaging/worker/inbound", "GET", worker=True) == 90


@pytest.mark.asyncio
async def test_repeated_cancel_is_idempotent_and_does_not_duplicate_event(tmp_path, monkeypatch) -> None:
    from amora.conversation.models import UtteranceKind
    from amora.events import PrivacyScope

    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        utterance = await core.conversation.enqueue_text(
            "Fala de teste", target_id="PERSON_BK", kind=UtteranceKind.DIALOGUE,
            priority=30, privacy_scope=PrivacyScope.PERSONAL,
            source_event_id=None, dedupe_key="repeat-cancel-test",
        )
        assert utterance is not None
        before = core.database.event_count()
        first, count = await core.conversation.cancel_utterance(utterance.id, reason="barge_in")
        assert first is not None and count == 1
        assert first.status.value == "cancelled"
        assert core.database.event_count() == before + 1
        second, count = await core.conversation.cancel_utterance(utterance.id, reason="barge_in")
        assert second is not None and count == 0
        assert core.database.event_count() == before + 1
    finally:
        core.close()


def test_libsql_safe_event_dedupe_uses_atomic_conflict_target(tmp_path, monkeypatch) -> None:
    from amora.events import AmoraEvent
    from amora.storage import AmoraDatabase

    db = AmoraDatabase(tmp_path / "events.db")
    db.migrate()
    try:
        first = AmoraEvent(event_type="conversation.utterance_cancelled",
                           source="test", dedupe_key="cancel:once")
        second = AmoraEvent(event_type="conversation.utterance_cancelled",
                            source="test", dedupe_key="cancel:once")
        assert db.append_event(first) is True
        assert db.append_event(second) is False
        assert db.event_count() == 1
        sql = db._conn.execute(
            "SELECT event_type FROM events WHERE dedupe_key=?", ("cancel:once",)
        ).fetchone()
        assert sql["event_type"] == "conversation.utterance_cancelled"
    finally:
        db.close()
