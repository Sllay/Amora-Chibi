from __future__ import annotations

import hashlib
from pathlib import Path

from amora.app import AmoraCore
from amora.messaging import (
    AttentionDecision,
    ChannelType,
    ExpressionKind,
    ExpressionRef,
    ExpressionSemanticProfile,
    ExternalActorRef,
    InboundMessage,
    MessagingPlatform,
    ResponseModality,
    RoutingAction,
)
from amora.time import SimulatedClock


def _sticker(*, description: str = "chibi sorrindo") -> ExpressionRef:
    return ExpressionRef(
        kind=ExpressionKind.STICKER,
        native_id="smile-1",
        name="sorriso-1",
        description=description,
        tags=["smile", "happy"],
        format_type=1,
        guild_or_group_id="guild-1",
    )


def _message(sticker: ExpressionRef, *, message_id: str, inbound: bool) -> InboundMessage:
    return InboundMessage(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        conversation_id="channel-1",
        message_id=message_id,
        native_message_id=message_id,
        channel_type=ChannelType.GROUP,
        sender_identifiers=[
            ExternalActorRef(
                identifier_type="discord_user_id",
                external_actor_id="111111111111111111",
            )
        ],
        text="Amora, abre esse sorrisão pro pai" if not inbound else "",
        mentions_amora=True,
        expressions=[sticker] if inbound else [],
        available_expressions=[sticker],
        timestamp=SimulatedClock().now(),
        guild_or_group_id="guild-1",
    )


def _decision() -> AttentionDecision:
    return AttentionDecision(
        action=RoutingAction.RESPOND,
        score=1.0,
        speaker_actor_ref="discord:111111111111111111",
        addressee_id="ENTITY_AMORA",
        reason_codes=["TEST"],
    )


def _profile() -> ExpressionSemanticProfile:
    return ExpressionSemanticProfile(
        short_description="Chibi com sorriso enorme e olhar pirado; zoeira caótica.",
        visual_concepts=["chibi", "sorriso enorme", "olhar pirado"],
        affect_tags=["joy", "playful"],
        social_roles=["playful", "affection"],
        possible_interpretations=["zoeira", "sorriso da loucura"],
        intensity=0.9,
        valence=0.7,
        arousal=0.9,
        confidence=0.96,
    )


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap(clock=SimulatedClock())


def _claim_and_bind(memory, *, body: bytes = b"hardening-visual-v1"):
    claims = memory.claim_visual(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
    )
    assert len(claims) == 1
    claim = claims[0]
    content_sha = hashlib.sha256(body).hexdigest()
    assert memory.record_visual_content(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        scope_id=claim.record.scope_id,
        kind=claim.record.kind,
        native_id=claim.record.native_id,
        claim_token=claim.claim_token,
        expected_fingerprint=claim.record.fingerprint,
        content_fingerprint=content_sha,
        content_mime="image/png",
        content_size_bytes=len(body),
    ) is True
    return claim, content_sha


def _apply(memory, claim, content_sha: str) -> bool:
    return memory.apply_visual_enrichment(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        scope_id=claim.record.scope_id,
        kind=claim.record.kind,
        native_id=claim.record.native_id,
        profile=_profile(),
        expected_fingerprint=claim.record.fingerprint,
        claim_token=claim.claim_token,
        content_fingerprint=content_sha,
        provider="test-provider",
        model="test-vision",
    )


def test_same_visual_enrichment_retry_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        sticker = _sticker()
        memory = core.messaging.expression_memory
        memory.observe_relevant(_message(sticker, message_id="m1", inbound=True), _decision())
        claim, content_sha = _claim_and_bind(memory)

        assert _apply(memory, claim, content_sha) is True
        once = memory.get(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            scope_id="guild-1",
            kind="sticker",
            native_id="smile-1",
        )
        assert once is not None
        assert once.semantic_revision == 1

        # Network retry after the successful commit is accepted even though the
        # lease was already cleared; semantic_revision must not increment twice.
        assert _apply(memory, claim, content_sha) is True
        twice = memory.get(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            scope_id="guild-1",
            kind="sticker",
            native_id="smile-1",
        )
        assert twice is not None
        assert twice.semantic_revision == 1
    finally:
        core.close()


def test_changed_catalog_metadata_quarantines_old_semantics_before_observation(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        original = _sticker(description="chibi sorrindo")
        memory = core.messaging.expression_memory
        memory.observe_relevant(_message(original, message_id="m1", inbound=True), _decision())
        claim, content_sha = _claim_and_bind(memory)
        assert _apply(memory, claim, content_sha) is True

        changed = _sticker(description="conteúdo/metadata agora diferente")
        hydrated = memory.hydrate(_message(changed, message_id="later", inbound=False))
        candidate = hydrated.available_expressions[0]

        assert candidate.known_to_amora is True
        assert candidate.needs_visual_enrichment is True
        assert candidate.semantic_profile is None
        decision = core.messaging.response_modality.select(hydrated, "hehe papis")
        assert decision.modality == ResponseModality.TEXT
        assert decision.expressions == []
    finally:
        core.close()


def test_stale_record_is_not_autonomously_selected_until_fresh_enrichment(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        original = _sticker(description="chibi sorrindo")
        memory = core.messaging.expression_memory
        memory.observe_relevant(_message(original, message_id="m1", inbound=True), _decision())
        claim, content_sha = _claim_and_bind(memory)
        assert _apply(memory, claim, content_sha) is True

        changed = _sticker(description="chibi completamente diferente")
        memory.observe_relevant(_message(changed, message_id="m2", inbound=True), _decision())
        hydrated = memory.hydrate(_message(changed, message_id="later", inbound=False))

        assert hydrated.available_expressions[0].semantic_profile is None
        assert hydrated.available_expressions[0].needs_visual_enrichment is True
        decision = core.messaging.response_modality.select(hydrated, "hehe papis")
        assert decision.modality == ResponseModality.TEXT
        assert decision.expressions == []
    finally:
        core.close()
