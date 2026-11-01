from __future__ import annotations

import pytest

from amora.app import AmoraCore
from amora.messaging import (
    ChannelType,
    ExternalActorRef,
    InboundMessage,
    MessagingPlatform,
    RoutingAction,
)
from amora.time import SimulatedClock


@pytest.mark.asyncio
async def test_unknown_group_native_discord_mention_reaches_cognition_and_queues_reply(
    tmp_path, monkeypatch
) -> None:
    """A native @Amora mention must not require a pre-existing PERSON binding."""
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        result = await core.messaging.receive(
            InboundMessage(
                platform=MessagingPlatform.DISCORD,
                account_scope="primary",
                conversation_id="discord-staging-channel",
                message_id="discord-native-mention-1",
                channel_type=ChannelType.GROUP,
                sender_identifiers=[
                    ExternalActorRef(
                        identifier_type="discord_user_id",
                        external_actor_id="222222222222222222",
                    )
                ],
                text="olá",
                mentions_amora=True,
                timestamp=core.clock.now(),
                remote_processing_allowed=False,
            )
        )

        assert result.decision.action == RoutingAction.RESPOND
        assert "GROUP_NATIVE_MENTION_AMORA" in result.decision.reason_codes
        assert result.decision.speaker_entity_id is None
        assert len(result.outbound) == 1
        assert result.outbound[0].reply_to_message_id == "discord-native-mention-1"
        assert result.outbound[0].text.strip()
    finally:
        core.close()
