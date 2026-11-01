from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.messaging import (
    ChannelType,
    ExternalActorRef,
    InboundMessage,
    MessagingPlatform,
    OutboundStatus,
    RoutingAction,
    TransportInvalidation,
)
from amora.time import SimulatedClock


def _discord_message(
    core: AmoraCore,
    *,
    delivery_id: str,
    native_id: str,
    revision: int,
    text: str,
) -> InboundMessage:
    return InboundMessage(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        conversation_id="discord-channel-1",
        message_id=delivery_id,
        native_message_id=native_id,
        transport_revision=revision,
        channel_type=ChannelType.GROUP,
        sender_identifiers=[
            ExternalActorRef(
                identifier_type="discord_user_id",
                external_actor_id="222222222222222222",
            )
        ],
        text=text,
        mentions_amora=True,
        timestamp=core.clock.now(),
        remote_processing_allowed=False,
    )


@pytest.mark.asyncio
async def test_transport_edit_cancels_old_outbound_and_revision_replies_to_native_message(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        original = _discord_message(
            core,
            delivery_id="discord-native-1",
            native_id="discord-native-1",
            revision=1,
            text="Amora, primeira versão",
        )
        first = await core.messaging.receive(original)
        assert first.decision.action == RoutingAction.RESPOND
        assert len(first.outbound) == 1
        stale = first.outbound[0]
        assert stale.reply_to_message_id == "discord-native-1"
        assert stale.metadata["transport_revision"] == 1

        invalidated = core.messaging.invalidate_transport(
            TransportInvalidation(
                platform=MessagingPlatform.DISCORD,
                account_scope="primary",
                conversation_id="discord-channel-1",
                native_message_id="discord-native-1",
                reason="DISCORD_MESSAGE_UPDATED",
                transport_revision=2,
            )
        )
        assert invalidated["cancelled_outbound"] == 1
        assert not core.messaging.attention.floor.validate_reply(original, first.decision)

        row = core.database._conn.execute(
            "SELECT status, error_code FROM messaging_outbound_queue WHERE id=?",
            (str(stale.id),),
        ).fetchone()
        assert row["status"] == OutboundStatus.CANCELLED.value
        assert row["error_code"] == "DISCORD_MESSAGE_UPDATED"

        edited = _discord_message(
            core,
            delivery_id="discord-native-1#edit:2",
            native_id="discord-native-1",
            revision=2,
            text="Amora, versão corrigida",
        )
        second = await core.messaging.receive(edited)
        assert second.decision.action == RoutingAction.RESPOND
        assert len(second.outbound) == 1
        fresh = second.outbound[0]
        assert fresh.reply_to_message_id == "discord-native-1"
        assert fresh.source_message_id == "discord-native-1"
        assert fresh.metadata["transport_revision"] == 2
        assert fresh.metadata["delivery_message_id"] == "discord-native-1#edit:2"
    finally:
        core.close()


def test_worker_can_invalidate_and_cancel_but_messaging_token_remains_narrow(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_MESSAGING_SERVICE_TOKEN", "m" * 48)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        with TestClient(create_api(core)) as client:
            headers = {"Authorization": "Bearer " + "m" * 48}
            invalidated = client.post(
                "/v1/messaging/worker/invalidate",
                headers=headers,
                json={
                    "platform": "discord",
                    "account_scope": "primary",
                    "conversation_id": "channel-x",
                    "native_message_id": "message-x",
                    "reason": "DISCORD_MESSAGE_DELETED",
                    "transport_revision": 2,
                },
            )
            assert invalidated.status_code == 200, invalidated.text
            assert invalidated.json()["floor_generation"] == 1
            assert client.get("/v1/state", headers=headers).status_code == 403
            assert client.get("/v1/operator/messaging/audit", headers=headers).status_code == 403
    finally:
        core.close()
