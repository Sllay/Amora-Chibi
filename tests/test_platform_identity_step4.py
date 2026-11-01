from __future__ import annotations

import pytest
from pydantic import ValidationError

from amora.app import AmoraCore
from amora.cognition.models import CognitiveRequest
from amora.identity import BindingTrust, PlatformIdentityBinding, PlatformKind
from amora.messaging import ChannelType, ExternalActorRef, InboundMessage, MessagingPlatform
from amora.time import SimulatedClock


BK_SNOWFLAKE = "123456789012345678"


def _bind_bk(core: AmoraCore) -> None:
    core.profiles.upsert_binding(
        PlatformIdentityBinding(
            platform=PlatformKind.DISCORD,
            identifier_type="discord_user_id",
            external_actor_id=BK_SNOWFLAKE,
            entity_id="PERSON_BK",
            account_scope="primary",
            trust=BindingTrust.OPERATOR_VERIFIED,
            confidence=1.0,
        )
    )


def _discord_message(core: AmoraCore, *, actor_id: str, username: str) -> InboundMessage:
    return InboundMessage(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        conversation_id="identity-step4",
        message_id=f"m-{username}",
        channel_type=ChannelType.GROUP,
        sender_identifiers=[ExternalActorRef(identifier_type="discord_user_id", external_actor_id=actor_id)],
        sender_username=username,
        sender_display_name=username,
        text="Amora, quem sou eu?",
        mentions_amora=True,
        timestamp=core.clock.now(),
        remote_processing_allowed=True,
    )


def test_discord_snowflake_resolves_to_canonical_bk_even_when_username_changes(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        before = core.messaging.attention.route(_discord_message(core, actor_id=BK_SNOWFLAKE, username="badkingx."))
        after = core.messaging.attention.route(_discord_message(core, actor_id=BK_SNOWFLAKE, username="renamed_account"))
        assert before.speaker_entity_id == "PERSON_BK"
        assert after.speaker_entity_id == "PERSON_BK"
    finally:
        core.close()


def test_mutable_discord_username_cannot_be_saved_as_user_id() -> None:
    with pytest.raises(ValidationError, match="numeric user snowflake"):
        PlatformIdentityBinding(
            platform=PlatformKind.DISCORD,
            identifier_type="user_id",
            external_actor_id="badkingx.",
            entity_id="PERSON_BK",
        )


def test_unknown_discord_snowflake_remains_unknown(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        decision = core.messaging.attention.route(
            _discord_message(core, actor_id="999999999999999999", username="badkingx.")
        )
        assert decision.speaker_entity_id is None
    finally:
        core.close()


def test_cognition_projects_canonical_name_and_family_relation_without_platform_identifier(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        decision = core.messaging.attention.route(
            _discord_message(core, actor_id=BK_SNOWFLAKE, username="totally_different_username")
        )
        assert decision.speaker_entity_id == "PERSON_BK"
        context = core.cognition.compiler.compile(
            CognitiveRequest(
                text="quem sou eu pra você?",
                speaker_id=decision.speaker_entity_id,
                target_id="ENTITY_AMORA",
                remote_processing_allowed=True,
            )
        )
        assert context.dialogue_plan["speaker_identity"]["entity_id"] == "PERSON_BK"
        assert context.dialogue_plan["speaker_identity"]["canonical_name"] == "BK"
        assert any(
            relation["source"] == "PERSON_BK"
            and relation["relation_type"] == "father_of"
            and relation["target"] == "ENTITY_AMORA"
            for relation in context.relationships
        )
        serialized = context.model_dump_json()
        assert BK_SNOWFLAKE not in serialized
        assert "totally_different_username" not in serialized
    finally:
        core.close()
