from __future__ import annotations

from amora.app import AmoraCore
from amora.messaging import ChannelType, ExternalActorRef, InboundMessage, MessagingPlatform, RoutingAction
from amora.time import SimulatedClock


def _message(core: AmoraCore, *, message_id: str, text: str, mention: bool) -> InboundMessage:
    return InboundMessage(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        conversation_id="discord-continuation-channel",
        message_id=message_id,
        channel_type=ChannelType.GROUP,
        sender_identifiers=[
            ExternalActorRef(
                identifier_type="discord_user_id",
                external_actor_id="111111111111111111",
                confidence=1.0,
            )
        ],
        text=text,
        mentions_amora=mention,
        timestamp=core.clock.now(),
        remote_processing_allowed=False,
    )


def test_same_speaker_continuation_supersedes_pending_reply_without_renewing_floor(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        direct = _message(core, message_id="direct-1", text="Amora, vem cá", mention=True)
        first = core.messaging.attention.route(direct)
        original_floor = core.messaging.attention.floor.active(direct)
        assert first.action == RoutingAction.RESPOND
        assert original_floor is not None

        continuation = _message(core, message_id="continuation-1", text="e mais uma coisa", mention=False)
        second = core.messaging.attention.route(continuation)
        continued_floor = core.messaging.attention.floor.active(continuation)

        assert second.action == RoutingAction.RESPOND
        assert second.floor_generation > first.floor_generation
        assert "FLOOR_GENERATION_ADVANCED" in second.reason_codes
        assert continued_floor is not None
        assert continued_floor.expires_at == original_floor.expires_at
        assert not core.messaging.attention.floor.validate_reply(direct, first)
        assert core.messaging.attention.floor.validate_reply(continuation, second)
    finally:
        core.close()
