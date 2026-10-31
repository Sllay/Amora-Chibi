from __future__ import annotations

import httpx

from amora.app import AmoraCore
from amora.messaging import (
    AuditDirection,
    ChannelType,
    MessagingAuditRecord,
    MessagingPlatform,
    OutboundMessage,
    PresenceCommand,
    PresenceState,
    RoutingAction,
)
from amora.storage.remote import LibSQLBackend
from amora.time import SimulatedClock
from test_remote_storage import HranaSQLite


def test_messaging_audit_outbound_and_presence_survive_remote_compute_replacement(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_MODE", "cloud")
    monkeypatch.setenv("AMORA_PUBLIC_ORIGINS", "https://amora.example")
    monkeypatch.setenv("AMORA_SECRET_LOCAL_API_TOKEN", "remote-messaging-admin-token-32-bytes-minimum")
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "first-compute"))

    server = HranaSQLite(tmp_path / "remote.db")
    backend = LibSQLBackend(
        "https://database.example",
        "database-test-only",
        transport=httpx.MockTransport(server),
    )
    clock = SimulatedClock()
    first = AmoraCore.bootstrap(clock=clock, storage=backend)
    try:
        assert first.database.schema_version() == 13
        audit = MessagingAuditRecord(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            conversation_id="private-channel-123",
            message_id="remote-inbound-1",
            direction=AuditDirection.INBOUND,
            channel_type=ChannelType.DM,
            sender_external_id="123456789012345678",
            speaker_entity_id="PERSON_BK",
            text="mensagem persistida remotamente",
            routing_action=RoutingAction.RESPOND,
            reason_codes=["REMOTE_TEST"],
            timestamp=clock.now(),
        )
        assert first.messaging_store.append_audit(audit)

        outbound = OutboundMessage(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            conversation_id="private-channel-123",
            target_entity_id="PERSON_BK",
            text="resposta persistida",
            reply_to_message_id="remote-inbound-1",
            source_message_id="remote-inbound-1",
            created_at=clock.now(),
        )
        assert first.messaging_store.put_outbound(outbound)

        requested = first.platform_presence.request(
            MessagingPlatform.DISCORD,
            PresenceCommand.CONNECT,
        )
        first.platform_presence.report(
            MessagingPlatform.DISCORD,
            PresenceState.CONNECTING,
            generation=requested.generation,
        )
        first.platform_presence.report(
            MessagingPlatform.DISCORD,
            PresenceState.CONNECTED,
            generation=requested.generation,
        )
    finally:
        first.close()

    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "replacement-compute"))
    second = AmoraCore.bootstrap(clock=clock, storage=backend)
    try:
        assert second.database.schema_version() == 13
        records = second.messaging_store.list_audit(limit=20)
        recovered = next(item for item in records if item.message_id == "remote-inbound-1")
        assert recovered.text == "mensagem persistida remotamente"
        assert recovered.sender_external_id == "123456789012345678"
        assert recovered.speaker_entity_id == "PERSON_BK"

        claimed = second.messaging_store.claim_outbound(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
        )
        assert [item.id for item in claimed] == [outbound.id]
        assert claimed[0].reply_to_message_id == "remote-inbound-1"

        presence = second.platform_presence.snapshot(MessagingPlatform.DISCORD)
        assert presence.desired_connected is True
        assert presence.state == PresenceState.DISCONNECTED
        assert presence.last_error_code == "CORE_RESTART_TRANSPORT_UNKNOWN"
    finally:
        second.close()
    assert not server.streams
