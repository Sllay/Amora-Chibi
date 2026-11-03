from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.events import PrivacyScope
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
from amora.messaging.visual_enrichment import (
    VisualAnalysisResult,
    VisualEnrichmentError,
    VisualEnrichmentStatus,
    VisualExpressionEnrichmentService,
)
from amora.time import SimulatedClock


PNG = b"\x89PNG\r\n\x1a\n" + b"visual-payload"


class FakeAnalyzer:
    remote_processing = True

    def __init__(self, *, fail: bool = False) -> None:
        self.fail = fail
        self.calls = 0
        self.seen: list[tuple[bytes, str]] = []

    def supports_mime(self, mime: str) -> bool:
        return mime == "image/png"

    async def analyze(self, *, content, mime, name, description, tags):
        self.calls += 1
        self.seen.append((content, mime))
        if self.fail:
            raise TimeoutError("simulated provider timeout")
        return VisualAnalysisResult(
            profile=ExpressionSemanticProfile(
                short_description="Chibi sorrindo com energia de zoeira.",
                visual_concepts=["chibi", "sorriso"],
                affect_tags=["joy", "playful"],
                social_roles=["playful"],
                possible_interpretations=["zoeira"],
                intensity=0.8,
                valence=0.8,
                arousal=0.7,
                confidence=0.95,
            ),
            provider="fake-provider",
            model="fake-vision",
        )


def _sticker(*, description: str = "chibi sorrindo", format_type: int = 1) -> ExpressionRef:
    return ExpressionRef(
        kind=ExpressionKind.STICKER,
        native_id="123456789012345678",
        name="sorriso",
        description=description,
        tags=["smile", "happy"],
        format_type=format_type,
        guild_or_group_id="guild-1",
    )


def _message(
    sticker: ExpressionRef,
    *,
    message_id: str = "m1",
    remote_processing_allowed: bool = True,
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL,
) -> InboundMessage:
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
        remote_processing_allowed=remote_processing_allowed,
        privacy_scope=privacy_scope,
    )


def _decision() -> AttentionDecision:
    return AttentionDecision(
        action=RoutingAction.RESPOND,
        score=1.0,
        speaker_actor_ref="discord:111111111111111111",
        addressee_id="ENTITY_AMORA",
        reason_codes=["TEST"],
    )


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap(clock=SimulatedClock())


def _observe(core: AmoraCore, service: VisualExpressionEnrichmentService, message: InboundMessage) -> None:
    core.messaging.expression_memory.observe_relevant(message, _decision())
    service.observe_policy(message, _decision())


def _claim(service: VisualExpressionEnrichmentService):
    claims = service.claim(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
    )
    assert len(claims) == 1
    return claims[0]


async def _ingest(service: VisualExpressionEnrichmentService, claim, *, payload: bytes = PNG):
    return await service.ingest_base64(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        scope_id=claim.record.scope_id,
        kind=claim.record.kind,
        native_id=claim.record.native_id,
        expected_fingerprint=claim.record.fingerprint,
        claim_token=claim.claim_token,
        content_mime="image/png",
        content_base64=base64.b64encode(payload).decode("ascii"),
    )


@pytest.mark.asyncio
async def test_allowed_remote_visual_analysis_commits_core_computed_sha(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    analyzer = FakeAnalyzer()
    service = VisualExpressionEnrichmentService(core.messaging.expression_memory, analyzer=analyzer)
    try:
        message = _message(_sticker(), remote_processing_allowed=True)
        _observe(core, service, message)
        claim = _claim(service)

        result = await _ingest(service, claim)

        assert result.status == VisualEnrichmentStatus.ENRICHED
        assert result.content_fingerprint == hashlib.sha256(PNG).hexdigest()
        assert analyzer.calls == 1
        record = core.messaging.expression_memory.get(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            scope_id="guild-1",
            kind="sticker",
            native_id="123456789012345678",
        )
        assert record is not None
        assert record.semantic_revision == 1
        assert record.content_fingerprint == hashlib.sha256(PNG).hexdigest()
        assert record.visual_provider == "fake-provider"
        assert record.visual_model == "fake-vision"
    finally:
        core.close()


@pytest.mark.asyncio
async def test_remote_processing_flag_denies_provider_before_any_call(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    analyzer = FakeAnalyzer()
    service = VisualExpressionEnrichmentService(core.messaging.expression_memory, analyzer=analyzer)
    try:
        message = _message(_sticker(), remote_processing_allowed=False)
        _observe(core, service, message)
        claim = _claim(service)

        result = await _ingest(service, claim)

        assert result.status == VisualEnrichmentStatus.DEFERRED
        assert result.reason_code == "REMOTE_PROCESSING_DENIED"
        assert analyzer.calls == 0
        with core.database._lock:
            row = core.database._conn.execute(
                """SELECT remote_processing_allowed, privacy_scope, visual_last_error_code
                   FROM perceptual_expression_assets WHERE native_id=?""",
                ("123456789012345678",),
            ).fetchone()
        assert int(row["remote_processing_allowed"]) == 0
        assert row["privacy_scope"] == PrivacyScope.PERSONAL.value
        assert row["visual_last_error_code"] == "REMOTE_PROCESSING_DENIED"
    finally:
        core.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("scope", [PrivacyScope.PRIVATE, PrivacyScope.SECRET, PrivacyScope.AUTH])
async def test_sensitive_scope_denies_remote_provider_even_when_flag_is_true(
    tmp_path: Path,
    monkeypatch,
    scope: PrivacyScope,
) -> None:
    core = _core(tmp_path, monkeypatch)
    analyzer = FakeAnalyzer()
    service = VisualExpressionEnrichmentService(core.messaging.expression_memory, analyzer=analyzer)
    try:
        message = _message(
            _sticker(),
            remote_processing_allowed=True,
            privacy_scope=scope,
        )
        _observe(core, service, message)
        result = await _ingest(service, _claim(service))

        assert result.status == VisualEnrichmentStatus.DEFERRED
        assert result.reason_code == "REMOTE_PROCESSING_DENIED"
        assert analyzer.calls == 0
    finally:
        core.close()


@pytest.mark.asyncio
async def test_same_content_sha_after_metadata_revision_reuses_without_second_provider_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    core = _core(tmp_path, monkeypatch)
    analyzer = FakeAnalyzer()
    service = VisualExpressionEnrichmentService(core.messaging.expression_memory, analyzer=analyzer)
    try:
        original = _message(_sticker(description="primeira metadata"), message_id="m1")
        _observe(core, service, original)
        first = await _ingest(service, _claim(service))
        assert first.status == VisualEnrichmentStatus.ENRICHED
        assert analyzer.calls == 1

        revised = _message(_sticker(description="metadata revisada"), message_id="m2")
        _observe(core, service, revised)
        second = await _ingest(service, _claim(service))

        assert second.status == VisualEnrichmentStatus.REUSED
        assert second.semantic_revision == 1
        assert analyzer.calls == 1
    finally:
        core.close()


def test_observe_policy_refreshes_format_type_and_privacy_metadata(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    service = VisualExpressionEnrichmentService(core.messaging.expression_memory)
    try:
        first = _message(_sticker(format_type=1), message_id="m1", remote_processing_allowed=False)
        _observe(core, service, first)
        second = _message(
            _sticker(description="revisado", format_type=4),
            message_id="m2",
            remote_processing_allowed=True,
            privacy_scope=PrivacyScope.SOCIAL,
        )
        _observe(core, service, second)

        with core.database._lock:
            row = core.database._conn.execute(
                """SELECT metadata_json, remote_processing_allowed, privacy_scope
                   FROM perceptual_expression_assets WHERE native_id=?""",
                ("123456789012345678",),
            ).fetchone()
        metadata = json.loads(row["metadata_json"])
        assert metadata["format_type"] == 4
        assert int(row["remote_processing_allowed"]) == 1
        assert row["privacy_scope"] == PrivacyScope.SOCIAL.value
    finally:
        core.close()


@pytest.mark.asyncio
async def test_provider_failure_defers_job_without_committing_semantics(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    analyzer = FakeAnalyzer(fail=True)
    service = VisualExpressionEnrichmentService(core.messaging.expression_memory, analyzer=analyzer)
    try:
        _observe(core, service, _message(_sticker()))
        claim = _claim(service)

        with pytest.raises(VisualEnrichmentError) as exc:
            await _ingest(service, claim)

        assert exc.value.code == "VISUAL_ANALYZER_FAILED"
        assert analyzer.calls == 1
        record = core.messaging.expression_memory.get(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            scope_id="guild-1",
            kind="sticker",
            native_id="123456789012345678",
        )
        assert record is not None
        assert record.semantic_revision == 0
        assert record.semantic_status.value == "failed"
        assert record.visual_last_error_code == "ANALYZER_TIMEOUTERROR"
    finally:
        core.close()


@pytest.mark.asyncio
async def test_core_rejects_invalid_base64_and_spoofed_png_before_analysis(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    analyzer = FakeAnalyzer()
    service = VisualExpressionEnrichmentService(core.messaging.expression_memory, analyzer=analyzer)
    try:
        _observe(core, service, _message(_sticker()))
        claim = _claim(service)

        with pytest.raises(VisualEnrichmentError) as exc:
            await service.ingest_base64(
                platform=MessagingPlatform.DISCORD,
                account_scope="primary",
                scope_id=claim.record.scope_id,
                kind=claim.record.kind,
                native_id=claim.record.native_id,
                expected_fingerprint=claim.record.fingerprint,
                claim_token=claim.claim_token,
                content_mime="image/png",
                content_base64="%%%not-base64%%%",
            )
        assert exc.value.code == "INVALID_VISUAL_BASE64"
        assert analyzer.calls == 0

        with pytest.raises(VisualEnrichmentError) as exc:
            await _ingest(service, claim, payload=b"<html>not a png</html>")
        assert exc.value.code == "INVALID_VISUAL_CONTENT"
        assert analyzer.calls == 0
    finally:
        core.close()
