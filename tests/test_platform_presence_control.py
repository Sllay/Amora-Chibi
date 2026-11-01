from __future__ import annotations

import pytest

from amora.app import AmoraCore
from amora.identity import BindingTrust, PlatformIdentityBinding, PlatformKind
from amora.messaging import (
    ChannelType,
    ExternalActorRef,
    InboundMessage,
    MessagingPlatform,
    PresenceState,
    RoutingAction,
)
from amora.time import SimulatedClock


def _bind(core: AmoraCore, entity_id: str, actor_id: str) -> None:
    core.profiles.upsert_binding(
        PlatformIdentityBinding(
            platform=PlatformKind.DISCORD,
            identifier_type="discord_user_id",
            external_actor_id=actor_id,
            entity_id=entity_id,
            account_scope="primary",
            trust=BindingTrust.OPERATOR_VERIFIED,
            confidence=1.0,
        )
    )


def _dm(core: AmoraCore, actor_id: str, message_id: str, text: str) -> InboundMessage:
    return InboundMessage(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        conversation_id=f"dm-{actor_id}",
        message_id=message_id,
        native_message_id=message_id,
        channel_type=ChannelType.DM,
        sender_identifiers=[
            ExternalActorRef(
                identifier_type="discord_user_id",
                external_actor_id=actor_id,
            )
        ],
        text=text,
        timestamp=core.clock.now(),
        remote_processing_allowed=False,
    )


@pytest.mark.asyncio
async def test_authorized_bk_can_connect_disconnect_check_and_query_status_without_provider(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        assert core.profiles.profile("PERSON_BK").can_control_platform_presence is True
        _bind(core, "PERSON_BK", "111111111111111111")
        cognition_before = len(core.cognition.store.list_messages(limit=500))

        connect = await core.messaging.receive(
            _dm(core, "111111111111111111", "presence-1", "Amora, conecta no Discord")
        )
        assert connect.decision.action == RoutingAction.RESPOND
        assert len(connect.outbound) == 1
        assert connect.outbound[0].metadata["local_control"] is True
        assert connect.outbound[0].metadata["presence_control_action"] == "connect"
        assert core.platform_presence.snapshot(MessagingPlatform.DISCORD).desired_connected is True

        status = await core.messaging.receive(
            _dm(core, "111111111111111111", "presence-2", "status do Discord")
        )
        assert len(status.outbound) == 1
        assert status.outbound[0].metadata["presence_control_action"] == "status"

        check = await core.messaging.receive(
            _dm(core, "111111111111111111", "presence-3", "checa o Discord")
        )
        assert core.platform_presence.snapshot(MessagingPlatform.DISCORD).check_requested is True
        assert check.outbound[0].metadata["presence_control_action"] == "check"

        disconnect = await core.messaging.receive(
            _dm(core, "111111111111111111", "presence-4", "desconecta do Discord")
        )
        snapshot = core.platform_presence.snapshot(MessagingPlatform.DISCORD)
        assert snapshot.desired_connected is False
        assert snapshot.check_requested is False
        assert disconnect.outbound[0].metadata["presence_control_action"] == "disconnect"

        cognition_after = len(core.cognition.store.list_messages(limit=500))
        assert cognition_after == cognition_before
    finally:
        core.close()


@pytest.mark.asyncio
async def test_known_person_without_capability_cannot_mutate_presence(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        lucas = core.profiles.profile("PERSON_LUCAS")
        assert lucas is not None
        assert lucas.can_control_platform_presence is False
        _bind(core, "PERSON_LUCAS", "222222222222222222")
        before = core.platform_presence.snapshot(MessagingPlatform.DISCORD)
        cognition_before = len(core.cognition.store.list_messages(limit=500))

        result = await core.messaging.receive(
            _dm(core, "222222222222222222", "presence-denied", "Amora, conecta no Discord")
        )
        after = core.platform_presence.snapshot(MessagingPlatform.DISCORD)
        assert result.decision.action == RoutingAction.RESPOND
        assert len(result.outbound) == 1
        assert result.outbound[0].metadata["presence_control_authorized"] is False
        assert after.desired_connected == before.desired_connected
        assert after.generation == before.generation
        assert len(core.cognition.store.list_messages(limit=500)) == cognition_before
    finally:
        core.close()


@pytest.mark.asyncio
async def test_unknown_dm_never_reaches_presence_control(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        before = core.platform_presence.snapshot(MessagingPlatform.DISCORD)
        result = await core.messaging.receive(
            _dm(core, "999999999999999999", "presence-unknown", "Amora, conecta no Discord")
        )
        after = core.platform_presence.snapshot(MessagingPlatform.DISCORD)
        assert result.decision.action == RoutingAction.QUARANTINE
        assert result.outbound == []
        assert after.desired_connected == before.desired_connected
        assert after.generation == before.generation
    finally:
        core.close()


def test_presence_control_parser_rejects_voice_call_phrases_and_ambiguous_platform_chat(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        parser = core.messaging.presence_control
        assert parser.parse("Amora, entra na call do Discord") is None
        assert parser.parse("a call do Discord caiu") is None
        assert parser.parse("o Discord tá uma desgraça hoje") is None
        assert parser.parse("Amora, conecta no Discord").action.value == "connect"
        assert parser.parse("desconecta do WhatsApp").platform.value == "whatsapp"
    finally:
        core.close()
