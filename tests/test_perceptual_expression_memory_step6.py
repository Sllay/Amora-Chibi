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
    ExpressionSemanticStatus,
    ExternalActorRef,
    InboundMessage,
    MessagingPlatform,
    ResponseModality,
    RoutingAction,
)
from amora.storage import LATEST_SCHEMA_VERSION
from amora.time import SimulatedClock


def _sticker(
    *,
    native_id: str = "smile-1",
    name: str = "sorriso-1",
    description: str = "chibi sorrindo",
    tags: tuple[str, ...] = ("smile", "happy"),
) -> ExpressionRef:
    return ExpressionRef(
        kind=ExpressionKind.STICKER,
        native_id=native_id,
        name=name,
        description=description,
        tags=list(tags),
        format_type=1,
        guild_or_group_id="guild-1",
    )


def _message(
    *,
    message_id: str = "message-1",
    sender_id: str = "111111111111111111",
    expression: ExpressionRef | None = None,
    catalog: list[ExpressionRef] | None = None,
    text: str = "Amora, olha isso",
) -> InboundMessage:
    expressions = [expression] if expression is not None else []
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
                external_actor_id=sender_id,
            )
        ],
        text=text,
        mentions_amora=True,
        expressions=expressions,
        available_expressions=catalog or [],
        timestamp=SimulatedClock().now(),
        guild_or_group_id="guild-1",
    )


def _decision(
    *,
    action: RoutingAction = RoutingAction.RESPOND,
    speaker_ref: str = "discord:111111111111111111",
) -> AttentionDecision:
    return AttentionDecision(
        action=action,
        score=1.0,
        speaker_actor_ref=speaker_ref,
        speaker_entity_id=None,
        addressee_id="ENTITY_AMORA",
        reason_codes=["TEST"],
    )


def _core(tmp_path: Path, monkeypatch, *, clock: SimulatedClock | None = None) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap(clock=clock or SimulatedClock())


def _profile() -> ExpressionSemanticProfile:
    return ExpressionSemanticProfile(
        short_description="Chibi com sorriso enorme e olhar pirado; energia de zoeira caótica.",
        visual_concepts=["chibi", "sorriso enorme", "olhar pirado"],
        affect_tags=["joy", "playful"],
        social_roles=["playful", "affection"],
        possible_interpretations=["zoeira", "sorriso da loucura"],
        intensity=0.9,
        valence=0.7,
        arousal=0.9,
        confidence=0.96,
    )


def _claim(memory):
    claims = memory.claim_visual(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
    )
    assert len(claims) == 1
    return claims[0]


def _bind(memory, claim, *, body: bytes = b"visual-sticker-v1") -> str:
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
    return content_sha


def _enrich(memory, *, profile: ExpressionSemanticProfile | None = None, body: bytes = b"visual-sticker-v1"):
    claim = _claim(memory)
    content_sha = _bind(memory, claim, body=body)
    assert memory.apply_visual_enrichment(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        scope_id=claim.record.scope_id,
        kind=claim.record.kind,
        native_id=claim.record.native_id,
        profile=profile or _profile(),
        expected_fingerprint=claim.record.fingerprint,
        claim_token=claim.claim_token,
        content_fingerprint=content_sha,
        provider="test-provider",
        model="test-vision",
    ) is True
    return claim, content_sha


def test_latest_schema_contains_perceptual_visual_enrichment_fields(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        assert core.database.schema_version() == LATEST_SCHEMA_VERSION == 18
        columns = {
            str(row[1])
            for row in core.database._conn.execute(
                "PRAGMA table_info(perceptual_expression_assets)"
            ).fetchall()
        }
        for name in {
            "content_fingerprint",
            "content_mime",
            "content_size_bytes",
            "last_visual_analysis_content_fingerprint",
            "visual_provider",
            "visual_model",
            "visual_claim_token",
            "visual_claimed_until",
            "visual_attempt_count",
            "visual_last_error_code",
            "visual_defer_until",
        }:
            assert name in columns
    finally:
        core.close()


def test_available_catalog_does_not_teach_amora_by_itself(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        sticker = _sticker()
        message = _message(catalog=[sticker], expression=None, text="Amora, abre esse sorrisão pro pai")

        hydrated = core.messaging.expression_memory.hydrate(message)

        assert core.messaging.expression_memory.known_count() == 0
        assert hydrated.available_expressions[0].known_to_amora is False
        decision = core.messaging.response_modality.select(hydrated, "hehe, papis")
        assert decision.modality == ResponseModality.TEXT
        assert decision.expressions == []
    finally:
        core.close()


def test_relevant_inbound_expression_creates_persistent_cpf(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        sticker = _sticker()
        message = _message(expression=sticker, catalog=[sticker], text="")

        hydrated = core.messaging.expression_memory.observe_relevant(message, _decision())
        record = core.messaging.expression_memory.get(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            scope_id="guild-1",
            kind="sticker",
            native_id="smile-1",
        )

        assert record is not None
        assert record.semantic_status == ExpressionSemanticStatus.PENDING_VISUAL
        assert record.seen_count == 1
        assert record.unique_senders_count == 1
        assert record.fingerprint_source == "transport_metadata"
        assert record.content_fingerprint is None
        assert record.needs_visual_enrichment is True
        assert record.semantic_profile is not None
        assert record.semantic_profile.source == "transport_metadata"
        assert record.semantic_profile.confidence == 0.20
        assert record.semantic_profile.short_description
        assert len(record.semantic_profile.short_description) <= 250
        assert hydrated.expressions[0].known_to_amora is True
        assert hydrated.expressions[0].memory_key == record.memory_key
        assert hydrated.expressions[0].needs_visual_enrichment is True
        assert hydrated.available_expressions[0].known_to_amora is True
    finally:
        core.close()


def test_replaying_same_source_message_does_not_inflate_familiarity(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        sticker = _sticker()
        message = _message(expression=sticker, catalog=[sticker], text="")
        memory = core.messaging.expression_memory

        memory.observe_relevant(message, _decision())
        memory.observe_relevant(message, _decision())
        record = memory.get(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            scope_id="guild-1",
            kind="sticker",
            native_id="smile-1",
        )

        assert record is not None
        assert record.seen_count == 1
        assert record.unique_senders_count == 1
    finally:
        core.close()


def test_new_message_and_new_sender_increase_social_familiarity(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        sticker = _sticker()
        memory = core.messaging.expression_memory
        memory.observe_relevant(
            _message(message_id="message-1", sender_id="111", expression=sticker, catalog=[sticker], text=""),
            _decision(speaker_ref="discord:111"),
        )
        memory.observe_relevant(
            _message(message_id="message-2", sender_id="222", expression=sticker, catalog=[sticker], text=""),
            _decision(speaker_ref="discord:222"),
        )
        record = memory.get(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            scope_id="guild-1",
            kind="sticker",
            native_id="smile-1",
        )

        assert record is not None
        assert record.seen_count == 2
        assert record.unique_senders_count == 2
    finally:
        core.close()


def test_non_respond_interaction_does_not_create_cpf(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        sticker = _sticker()
        message = _message(expression=sticker, catalog=[sticker], text="")

        hydrated = core.messaging.expression_memory.observe_relevant(
            message,
            _decision(action=RoutingAction.CONTEXT_ONLY),
        )

        assert core.messaging.expression_memory.known_count() == 0
        assert hydrated.expressions[0].known_to_amora is False
    finally:
        core.close()


def test_cpf_survives_restart_and_hydrates_future_catalog(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    sticker = _sticker()
    try:
        core.messaging.expression_memory.observe_relevant(
            _message(expression=sticker, catalog=[sticker], text=""),
            _decision(),
        )
    finally:
        core.close()

    core = _core(tmp_path, monkeypatch)
    try:
        future = _message(
            message_id="message-later",
            expression=None,
            catalog=[sticker],
            text="Amora, abre esse sorrisão pro pai",
        )
        hydrated = core.messaging.expression_memory.hydrate(future)

        assert core.messaging.expression_memory.known_count() == 1
        assert hydrated.available_expressions[0].known_to_amora is True
        assert hydrated.available_expressions[0].memory_key is not None
        assert hydrated.available_expressions[0].semantic_profile is not None
    finally:
        core.close()


def test_visual_enrichment_is_content_addressed_revisioned_and_reused(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        sticker = _sticker()
        memory = core.messaging.expression_memory
        memory.observe_relevant(
            _message(expression=sticker, catalog=[sticker], text=""),
            _decision(),
        )

        _, content_sha = _enrich(memory)

        after = memory.get(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            scope_id="guild-1",
            kind="sticker",
            native_id="smile-1",
        )
        assert after is not None
        assert after.semantic_status == ExpressionSemanticStatus.ENRICHED
        assert after.semantic_revision == 1
        assert after.needs_visual_enrichment is False
        assert after.content_fingerprint == content_sha
        assert after.last_visual_analysis_content_fingerprint == content_sha
        assert after.visual_provider == "test-provider"
        assert after.visual_model == "test-vision"
        assert after.semantic_profile is not None
        assert after.semantic_profile.source == "visual_perception"
        assert all(item.native_id != "smile-1" for item in memory.pending_visual())

        hydrated = memory.hydrate(
            _message(message_id="later", expression=None, catalog=[sticker], text="oi")
        )
        assert hydrated.available_expressions[0].semantic_revision == 1
        assert hydrated.available_expressions[0].semantic_profile is not None
        assert hydrated.available_expressions[0].semantic_profile.short_description == _profile().short_description
    finally:
        core.close()


def test_changed_metadata_invalidates_old_claim_and_result(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        memory = core.messaging.expression_memory
        original = _sticker(description="chibi sorrindo")
        memory.observe_relevant(
            _message(message_id="m1", expression=original, catalog=[original], text=""),
            _decision(),
        )
        claim = _claim(memory)
        content_sha = _bind(memory, claim)

        changed = _sticker(description="chibi com sorriso diferente e olhos arregalados")
        memory.observe_relevant(
            _message(message_id="m2", expression=changed, catalog=[changed], text=""),
            _decision(),
        )
        stale = memory.get(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            scope_id="guild-1",
            kind="sticker",
            native_id="smile-1",
        )
        assert stale is not None
        assert stale.fingerprint != claim.record.fingerprint
        assert stale.semantic_status == ExpressionSemanticStatus.STALE
        assert stale.needs_visual_enrichment is True

        assert memory.apply_visual_enrichment(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            scope_id="guild-1",
            kind="sticker",
            native_id="smile-1",
            profile=_profile(),
            expected_fingerprint=claim.record.fingerprint,
            claim_token=claim.claim_token,
            content_fingerprint=content_sha,
            provider="test-provider",
            model="test-vision",
        ) is False
        assert any(item.native_id == "smile-1" for item in memory.pending_visual())
    finally:
        core.close()


def test_production_selector_uses_only_known_available_enriched_assets(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        smile = _sticker()
        rage = _sticker(native_id="rage-1", name="raiva", description="bravo", tags=("angry", "rage"))
        catalog_only = _message(
            message_id="catalog-only",
            expression=None,
            catalog=[smile, rage],
            text="Amora, abre esse sorrisão pro pai",
        )
        assert core.messaging.response_modality.select(
            core.messaging.expression_memory.hydrate(catalog_only),
            "hehe papis",
        ).modality == ResponseModality.TEXT

        memory = core.messaging.expression_memory
        memory.observe_relevant(
            _message(message_id="learn", expression=smile, catalog=[smile, rage], text=""),
            _decision(),
        )
        _enrich(memory)

        hydrated = memory.hydrate(catalog_only)
        decision = core.messaging.response_modality.select(hydrated, "hehe papis")
        assert decision.modality in {ResponseModality.TEXT_EXPRESSION, ResponseModality.EXPRESSION}
        assert decision.expressions
        assert decision.expressions[0].native_id == "smile-1"
        assert all(item.native_id != "rage-1" for item in decision.expressions)
    finally:
        core.close()
