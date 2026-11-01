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


@pytest.mark.asyncio
async def test_messaging_cognition_hard_timeout_queues_local_fallback_instead_of_silence(
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
        assert outbound.text.strip()
        assert outbound.metadata["fallback_used"] is True
        assert outbound.metadata["provider"] is None
        assert outbound.metadata["remote_processing_allowed"] is True
        assert "MESSAGING_COGNITION_HARD_TIMEOUT" in outbound.metadata["cognitive_reason_codes"]
    finally:
        core.close()
