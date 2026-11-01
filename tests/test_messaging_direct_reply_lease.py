from __future__ import annotations

from datetime import timedelta

from amora.app import AmoraCore
from amora.messaging import (
    ChannelType,
    ExternalActorRef,
    InboundMessage,
    MessagingPlatform,
    RoutingAction,
    TransportInvalidation,
)
from amora.time import SimulatedClock


def _group_message(
    core: AmoraCore,
    *,
    message_id: str,
    actor_id: str,
    text: str,
    mention: bool = False,
) -> InboundMessage:
    return InboundMessage(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        conversation_id="discord-channel-lease",
        message_id=message_id,
        channel_type=ChannelType.GROUP,
        sender_identifiers=[
            ExternalActorRef(identifier_type="discord_user_id", external_actor_id=actor_id)
        ],
        text=text,
        mentions_amora=mention,
        timestamp=core.clock.now(),
        remote_processing_allowed=False,
    )


def _invalidation(message_id: str) -> TransportInvalidation:
    return TransportInvalidation(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        conversation_id="discord-channel-lease",
        native_message_id=message_id,
        reason="DISCORD_MESSAGE_UPDATED",
        transport_revision=2,
    )


def test_explicit_discord_mention_survives_normal_provider_latency(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = SimulatedClock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        message = _group_message(
            core,
            message_id="direct-mention-1",
            actor_id="111111111111111111",
            text="oi Amora",
            mention=True,
        )
        decision = core.messaging.attention.route(message)

        assert decision.action == RoutingAction.RESPOND
        assert "GROUP_NATIVE_MENTION_AMORA" in decision.reason_codes

        # The conversational floor itself is intentionally short (35 s), but a
        # concrete native mention must not be discarded merely because provider
        # generation took longer than that short floor.
        clock.advance(timedelta(seconds=40))
        assert core.messaging.attention.floor.validate_reply(message, decision)

        # The explicit reply is still bounded; stale output is suppressed.
        clock.advance(timedelta(seconds=51))
        assert not core.messaging.attention.floor.validate_reply(message, decision)
    finally:
        core.close()


def test_explicit_discord_mention_is_invalidated_when_floor_generation_changes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        direct = _group_message(
            core,
            message_id="direct-mention-2",
            actor_id="111111111111111111",
            text="Amora, responde",
            mention=True,
        )
        decision = core.messaging.attention.route(direct)
        assert core.messaging.attention.floor.validate_reply(direct, decision)

        other = _group_message(
            core,
            message_id="other-speaker-1",
            actor_id="222222222222222222",
            text="fulano, continua",
        )
        other.mentioned_actor_ids = ["333333333333333333"]
        shifted = core.messaging.attention.route(other)

        assert "FLOOR_SHIFTED_AWAY" in shifted.reason_codes
        assert not core.messaging.attention.floor.validate_reply(direct, decision)
    finally:
        core.close()


def test_inferred_same_speaker_continuity_keeps_short_reply_lease(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = SimulatedClock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        direct = _group_message(
            core,
            message_id="direct-mention-3",
            actor_id="111111111111111111",
            text="Amora, vem cá",
            mention=True,
        )
        first = core.messaging.attention.route(direct)
        assert first.action == RoutingAction.RESPOND

        continuation = _group_message(
            core,
            message_id="continuation-1",
            actor_id="111111111111111111",
            text="e outra coisa",
        )
        followup = core.messaging.attention.route(continuation)
        assert followup.action == RoutingAction.RESPOND
        assert "BOUNDED_CONTINUITY" in followup.reason_codes

        clock.advance(timedelta(seconds=11))
        assert not core.messaging.attention.floor.validate_reply(continuation, followup)
    finally:
        core.close()


def test_unrelated_transport_edit_does_not_cancel_active_direct_reply(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        direct = _group_message(
            core,
            message_id="direct-source-1",
            actor_id="111111111111111111",
            text="Amora, pensa nisso",
            mention=True,
        )
        decision = core.messaging.attention.route(direct)
        before = decision.floor_generation

        result = core.messaging.invalidate_transport(_invalidation("unrelated-message"))

        assert result["floor_invalidated"] is False
        assert result["floor_generation"] == before
        assert core.messaging.attention.floor.validate_reply(direct, decision)
    finally:
        core.close()


def test_edit_of_actual_response_source_invalidates_direct_and_continuation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        direct = _group_message(
            core,
            message_id="direct-source-2",
            actor_id="111111111111111111",
            text="Amora, conversa comigo",
            mention=True,
        )
        direct_decision = core.messaging.attention.route(direct)
        continuation = _group_message(
            core,
            message_id="continuation-source-2",
            actor_id="111111111111111111",
            text="e continua aqui",
        )
        continuation_decision = core.messaging.attention.route(continuation)
        assert continuation_decision.action == RoutingAction.RESPOND

        result = core.messaging.invalidate_transport(_invalidation("continuation-source-2"))

        assert result["floor_invalidated"] is True
        assert result["floor_generation"] > continuation_decision.floor_generation
        assert not core.messaging.attention.floor.validate_reply(direct, direct_decision)
        assert not core.messaging.attention.floor.validate_reply(continuation, continuation_decision)
    finally:
        core.close()
