from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path

import pytest

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


def _message(sticker: ExpressionRef, *, message_id: str) -> InboundMessage:
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
        text="",
        mentions_amora=True,
        expressions=[sticker],
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


def _profile(*, description: str = "Chibi rindo com energia de zoeira.") -> ExpressionSemanticProfile:
    return ExpressionSemanticProfile(
        short_description=description,
        visual_concepts=["chibi", "sorriso"],
        affect_tags=["joy", "playful"],
        social_roles=["playful"],
        possible_interpretations=["zoeira"],
        intensity=0.8,
        valence=0.8,
        arousal=0.7,
        confidence=0.95,
    )


def _core(tmp_path: Path, monkeypatch, clock: SimulatedClock) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap(clock=clock)


def _observe(memory, sticker: ExpressionRef, *, message_id: str) -> None:
    memory.observe_relevant(_message(sticker, message_id=message_id), _decision())


def _claim(memory, *, lease_seconds: int = 120):
    claims = memory.claim_visual(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        lease_seconds=lease_seconds,
    )
    assert len(claims) == 1
    return claims[0]


def _sha(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _bind(memory, claim, body: bytes) -> bool:
    return memory.record_visual_content(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        scope_id=claim.record.scope_id,
        kind=claim.record.kind,
        native_id=claim.record.native_id,
        claim_token=claim.claim_token,
        expected_fingerprint=claim.record.fingerprint,
        content_fingerprint=_sha(body),
        content_mime="image/png",
        content_size_bytes=len(body),
    )


def _commit(memory, claim, body: bytes, *, profile: ExpressionSemanticProfile | None = None) -> bool:
    return memory.apply_visual_enrichment(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        scope_id=claim.record.scope_id,
        kind=claim.record.kind,
        native_id=claim.record.native_id,
        profile=profile or _profile(),
        expected_fingerprint=claim.record.fingerprint,
        claim_token=claim.claim_token,
        content_fingerprint=_sha(body),
        provider="test-provider",
        model="test-vision",
    )


def _record(memory):
    record = memory.get(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        scope_id="guild-1",
        kind="sticker",
        native_id="smile-1",
    )
    assert record is not None
    return record


def test_visual_claim_is_exclusive_between_workers(tmp_path: Path, monkeypatch) -> None:
    clock = SimulatedClock()
    core = _core(tmp_path, monkeypatch, clock)
    try:
        memory = core.messaging.expression_memory
        _observe(memory, _sticker(), message_id="m1")

        first = _claim(memory)
        second = memory.claim_visual(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
        )

        assert second == ()
        assert first.claim_token
        assert _record(memory).visual_attempt_count == 1
    finally:
        core.close()


def test_expired_visual_lease_is_reclaimed_and_old_token_is_rejected(tmp_path: Path, monkeypatch) -> None:
    clock = SimulatedClock()
    core = _core(tmp_path, monkeypatch, clock)
    try:
        memory = core.messaging.expression_memory
        _observe(memory, _sticker(), message_id="m1")

        old_claim = _claim(memory, lease_seconds=1)
        clock.advance(timedelta(seconds=2))
        new_claim = _claim(memory, lease_seconds=30)

        assert new_claim.claim_token != old_claim.claim_token
        assert _record(memory).visual_attempt_count == 2
        assert _bind(memory, old_claim, b"old-worker") is False
        assert _bind(memory, new_claim, b"new-worker") is True
    finally:
        core.close()


def test_defer_visual_hides_work_until_retry_window(tmp_path: Path, monkeypatch) -> None:
    clock = SimulatedClock()
    core = _core(tmp_path, monkeypatch, clock)
    try:
        memory = core.messaging.expression_memory
        _observe(memory, _sticker(), message_id="m1")
        claim = _claim(memory)

        assert memory.defer_visual(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            scope_id=claim.record.scope_id,
            kind=claim.record.kind,
            native_id=claim.record.native_id,
            claim_token=claim.claim_token,
            expected_fingerprint=claim.record.fingerprint,
            error_code="PROVIDER_TIMEOUT",
            retry_after_seconds=60,
        ) is True
        assert memory.claim_visual(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
        ) == ()

        deferred = _record(memory)
        assert deferred.semantic_status.value == "failed"
        assert deferred.visual_last_error_code == "PROVIDER_TIMEOUT"
        assert deferred.visual_attempt_count == 1

        clock.advance(timedelta(seconds=61))
        retry = _claim(memory)
        assert retry.claim_token != claim.claim_token
        assert _record(memory).visual_attempt_count == 2
    finally:
        core.close()


def test_same_content_after_metadata_revision_reuses_semantics_without_provider_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = SimulatedClock()
    core = _core(tmp_path, monkeypatch, clock)
    try:
        memory = core.messaging.expression_memory
        body = b"same-visual-bytes"
        original = _sticker(description="chibi sorrindo")
        _observe(memory, original, message_id="m1")
        first = _claim(memory)
        assert _bind(memory, first, body) is True
        assert _commit(memory, first, body) is True
        before = _record(memory)
        assert before.semantic_revision == 1
        original_profile = before.semantic_profile
        assert original_profile is not None

        changed = _sticker(description="metadata revisada pelo Discord")
        _observe(memory, changed, message_id="m2")
        stale = _record(memory)
        assert stale.semantic_status.value == "stale"
        assert stale.fingerprint != first.record.fingerprint

        second = _claim(memory)
        # False means the exact content SHA is already semantically known and the
        # worker must skip the multimodal provider call.
        assert _bind(memory, second, body) is False

        reused = _record(memory)
        assert reused.semantic_status.value == "enriched"
        assert reused.semantic_revision == 1
        assert reused.content_fingerprint == _sha(body)
        assert reused.last_visual_analysis_content_fingerprint == _sha(body)
        assert reused.last_visual_analysis_fingerprint == second.record.fingerprint
        assert reused.semantic_profile == original_profile
        assert memory.claim_visual(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
        ) == ()
    finally:
        core.close()


def test_changed_content_requires_new_analysis_and_increments_semantic_revision(
    tmp_path: Path,
    monkeypatch,
) -> None:
    clock = SimulatedClock()
    core = _core(tmp_path, monkeypatch, clock)
    try:
        memory = core.messaging.expression_memory
        first_body = b"visual-v1"
        second_body = b"visual-v2"
        original = _sticker(description="chibi sorrindo")
        _observe(memory, original, message_id="m1")
        first = _claim(memory)
        assert _bind(memory, first, first_body) is True
        assert _commit(memory, first, first_body) is True

        changed = _sticker(description="metadata e imagem agora diferentes")
        _observe(memory, changed, message_id="m2")
        second = _claim(memory)
        assert _bind(memory, second, second_body) is True
        assert _commit(
            memory,
            second,
            second_body,
            profile=_profile(description="Chibi agora surpresa, com olhos arregalados."),
        ) is True

        record = _record(memory)
        assert record.semantic_revision == 2
        assert record.content_fingerprint == _sha(second_body)
        assert record.last_visual_analysis_content_fingerprint == _sha(second_body)
        assert record.semantic_profile is not None
        assert record.semantic_profile.short_description.startswith("Chibi agora surpresa")
    finally:
        core.close()


@pytest.mark.parametrize(
    ("content_fingerprint", "content_mime", "content_size_bytes"),
    [
        ("not-a-sha", "image/png", 10),
        ("0" * 64, "invalid-mime", 10),
        ("0" * 64, "image/png", 0),
        ("0" * 64, "image/png", 20 * 1024 * 1024 + 1),
    ],
)
def test_invalid_visual_content_contract_does_not_mutate_record(
    tmp_path: Path,
    monkeypatch,
    content_fingerprint: str,
    content_mime: str,
    content_size_bytes: int,
) -> None:
    clock = SimulatedClock()
    core = _core(tmp_path, monkeypatch, clock)
    try:
        memory = core.messaging.expression_memory
        _observe(memory, _sticker(), message_id="m1")
        claim = _claim(memory)
        before = _record(memory)

        with pytest.raises(ValueError):
            memory.record_visual_content(
                platform=MessagingPlatform.DISCORD,
                account_scope="primary",
                scope_id=claim.record.scope_id,
                kind=claim.record.kind,
                native_id=claim.record.native_id,
                claim_token=claim.claim_token,
                expected_fingerprint=claim.record.fingerprint,
                content_fingerprint=content_fingerprint,
                content_mime=content_mime,
                content_size_bytes=content_size_bytes,
            )

        after = _record(memory)
        assert after.content_fingerprint == before.content_fingerprint is None
        assert after.content_mime == before.content_mime is None
        assert after.content_size_bytes == before.content_size_bytes is None
        assert after.semantic_revision == before.semantic_revision == 0
    finally:
        core.close()
