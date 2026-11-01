from __future__ import annotations

from datetime import timedelta

from amora.app import AmoraCore
from amora.messaging import ChannelType, ExternalActorRef, InboundMessage, MessagingPlatform, RoutingAction
from amora.time import SimulatedClock


def _message(core: AmoraCore, message_id: str, actor_id: str, text: str, *, mention: bool = False, conversation_id: str = "shared-floor") -> InboundMessage:
    return InboundMessage(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        conversation_id=conversation_id,
        message_id=message_id,
        channel_type=ChannelType.GROUP,
        sender_identifiers=[ExternalActorRef(identifier_type="discord_user_id", external_actor_id=actor_id)],
        text=text,
        mentions_amora=mention,
        timestamp=core.clock.now(),
        remote_processing_allowed=False,
    )


def test_floor_survives_core_instance_boundary(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = SimulatedClock()
    first_core = AmoraCore.bootstrap(clock=clock)
    second_core = AmoraCore.bootstrap(clock=clock)
    try:
        direct = _message(first_core, "m1", "111111111111111111", "Amora, olha isso", mention=True)
        opened = first_core.messaging.attention.route(direct)
        assert opened.action == RoutingAction.RESPOND

        followup = _message(second_core, "m2", "111111111111111111", "e isso aqui também")
        continued = second_core.messaging.attention.route(followup)

        assert continued.action == RoutingAction.RESPOND
        assert "BOUNDED_CONTINUITY" in continued.reason_codes
        assert continued.floor_generation > opened.floor_generation
    finally:
        second_core.close()
        first_core.close()


def test_other_speaker_closes_shared_floor_for_every_instance(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = SimulatedClock()
    first_core = AmoraCore.bootstrap(clock=clock)
    second_core = AmoraCore.bootstrap(clock=clock)
    try:
        first_core.messaging.attention.route(
            _message(first_core, "m1", "111111111111111111", "Amora, vem cá", mention=True)
        )
        interruption = second_core.messaging.attention.route(
            _message(second_core, "m2", "222222222222222222", "conversa paralela")
        )
        assert interruption.action == RoutingAction.CONTEXT_ONLY
        assert "FLOOR_SHIFTED_AWAY" in interruption.reason_codes

        resumed = first_core.messaging.attention.route(
            _message(first_core, "m3", "111111111111111111", "continua")
        )
        assert resumed.action == RoutingAction.CONTEXT_ONLY
        assert "NO_DIRECT_ATTENTION" in resumed.reason_codes
    finally:
        second_core.close()
        first_core.close()


def test_shared_floor_expires_without_becoming_global_attention(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = SimulatedClock()
    first_core = AmoraCore.bootstrap(clock=clock)
    second_core = AmoraCore.bootstrap(clock=clock)
    try:
        first_core.messaging.attention.route(
            _message(first_core, "m1", "111111111111111111", "Amora, teste", mention=True)
        )
        clock.advance(timedelta(seconds=36))
        expired = second_core.messaging.attention.route(
            _message(second_core, "m2", "111111111111111111", "mensagem ambiente")
        )
        assert expired.action == RoutingAction.CONTEXT_ONLY
        assert "NO_DIRECT_ATTENTION" in expired.reason_codes
    finally:
        second_core.close()
        first_core.close()


def test_floor_isolated_by_conversation(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        core.messaging.attention.route(
            _message(core, "m1", "111111111111111111", "Amora, canal A", mention=True, conversation_id="channel-a")
        )
        other_channel = core.messaging.attention.route(
            _message(core, "m2", "111111111111111111", "canal B", conversation_id="channel-b")
        )
        assert other_channel.action == RoutingAction.CONTEXT_ONLY
        assert "NO_DIRECT_ATTENTION" in other_channel.reason_codes
    finally:
        core.close()


def test_schema_14_contains_shared_floor_table(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        assert core.database.schema_version() == 14
        row = core.database._conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name='messaging_conversation_floor'"
        ).fetchone()
        assert row is not None
        assert core.readiness()["status"] == "ready"
    finally:
        core.close()
