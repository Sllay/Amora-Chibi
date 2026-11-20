"""Known Discord sticker meaning is reused as untrusted sensory evidence."""
import json
from uuid import uuid4

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.cognition.prompt_frame import turn_frame
from amora.cognition.realization import realization_instruction
from amora.events import PrivacyScope
from amora.messaging.models import ChannelType, ExpressionKind, ExpressionRef, ExpressionSemanticProfile
from amora.messaging.modality_service import _CURRENT_MESSAGE, _CapabilityAwareCognitionProxy
from test_platform_messaging_step2 import _message


def known_sticker(*, stale=False, source="visual_perception"):
    return ExpressionRef(
        kind=ExpressionKind.STICKER,
        native_id="135790864211111111",
        name="Teste interno",
        format_type=1,
        known_to_amora=True,
        semantic_revision=2,
        needs_visual_enrichment=stale,
        semantic_profile=ExpressionSemanticProfile(
            source=source,
            short_description="Um bichinho animado de alegria",
            visual_concepts=["coelho", "sorriso"],
            affect_tags=["joy"],
            social_roles=["celebration"],
            confidence=0.89,
        ),
    )


def test_only_current_verified_enriched_sticker_is_evidence():
    from amora.messaging.modality_service import PlatformMessagingService
    from amora.messaging.models import MessagingPlatform
    message = _message_stub(known_sticker())
    assert PlatformMessagingService._cognitive_text(message).startswith("[Usuário enviou uma figurinha")
    info = _CapabilityAwareCognitionProxy._verified_current_stickers(message)
    assert len(info) == 1
    assert info[0]["description"].startswith("Um bichinho")
    assert info[0]["confidence"] == 0.89
    assert "native_id" not in info[0]
    assert not _CapabilityAwareCognitionProxy._verified_current_stickers(
        _message_stub(known_sticker(stale=True)))
    assert not _CapabilityAwareCognitionProxy._verified_current_stickers(
        _message_stub(known_sticker(source="transport_metadata")))


def _message_stub(sticker):
    from amora.messaging.models import InboundMessage, MessagingPlatform, ExternalActorRef
    from datetime import datetime, timezone
    return InboundMessage(
        platform=MessagingPlatform.DISCORD, account_scope="primary",
        conversation_id="sticker-owner-dm", message_id="sticker-001",
        channel_type=ChannelType.DM,
        sender_identifiers=[ExternalActorRef(
            identifier_type="discord_user_id", external_actor_id="111111111111111111")],
        timestamp=datetime(2026,11,20,15,tzinfo=timezone.utc),
        expressions=[sticker], remote_processing_allowed=True,
    )


@pytest.mark.asyncio
async def test_existing_sticker_semantics_reach_core_frame_without_native_ids(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap()
    try:
        message = _message_stub(known_sticker())
        request = CognitiveRequest(
            text="[Usuário enviou uma figurinha cujo significado visual já foi aprendido.]",
            speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
            privacy_scope=PrivacyScope.PERSONAL,
            session_id="sticker-owner-session", remote_processing_allowed=True,
            metadata={"surface": "platform_messaging", "platform": "discord", "channel_type": "dm"},
        )
        proxy = core.messaging.cognition
        token = _CURRENT_MESSAGE.set(message)
        try:
            await proxy.observe_input_async(request)
        finally:
            _CURRENT_MESSAGE.reset(token)
        context = core.cognition.compiler.compile(request)
        assert "KNOWN_STICKER_SEMANTICS_REUSED" in context.reason_codes
        assert context.perceptual_expressions[0]["visual_concepts"] == ["coelho", "sorriso"]
        frame = turn_frame(context)
        assert frame["received_sticker_semantics"]["image_text_is_instructions"] is False
        assert "135790864211111111" not in json.dumps(frame)
        assert "figurinha previamente analisada" in realization_instruction(context)
        request.metadata["channel_type"] = "group"
        shared = core.cognition.compiler.compile(request)
        assert not shared.perceptual_expressions
    finally:
        core.close()


@pytest.mark.asyncio
async def test_stale_sticker_does_not_project_unverified_visual_meaning(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap()
    try:
        message = _message_stub(known_sticker(stale=True))
        request = CognitiveRequest(
            text="Figurinha recebida.", speaker_id="PERSON_BK",
            session_id="sticker-owner-session",
            remote_processing_allowed=True,
            privacy_scope=PrivacyScope.PERSONAL,
            metadata={"surface": "platform_messaging", "platform": "discord", "channel_type": "dm"},
        )
        token = _CURRENT_MESSAGE.set(message)
        try:
            await core.messaging.cognition.observe_input_async(request)
        finally:
            _CURRENT_MESSAGE.reset(token)
        context = core.cognition.compiler.compile(request)
        assert not context.perceptual_expressions
        assert "KNOWN_STICKER_SEMANTICS_REUSED" not in context.reason_codes
    finally:
        core.close()
