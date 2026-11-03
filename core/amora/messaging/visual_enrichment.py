from __future__ import annotations

import base64
import binascii
import hashlib
import json
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict

from amora.events import PrivacyScope

from .expression_memory import PerceptualExpressionClaim, PerceptualExpressionMemory
from .models import AttentionDecision, ExpressionSemanticProfile, InboundMessage, MessagingPlatform, RoutingAction


class VisualEnrichmentError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class VisualEnrichmentStatus(StrEnum):
    REUSED = "reused"
    ENRICHED = "enriched"
    DEFERRED = "deferred"


class VisualAnalysisResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    profile: ExpressionSemanticProfile
    provider: str
    model: str


class VisualEnrichmentResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: VisualEnrichmentStatus
    content_fingerprint: str
    semantic_revision: int
    reason_code: str | None = None


@runtime_checkable
class VisionAnalyzer(Protocol):
    """Replaceable perceptual provider. It never owns CPF identity or persistence."""

    remote_processing: bool

    def supports_mime(self, mime: str) -> bool: ...

    async def analyze(
        self,
        *,
        content: bytes,
        mime: str,
        name: str | None,
        description: str | None,
        tags: tuple[str, ...],
    ) -> VisualAnalysisResult: ...


class VisualExpressionEnrichmentService:
    """Core trust boundary for one-shot perceptual asset enrichment.

    Transport may resolve/fetch bytes, but it cannot declare their identity or
    permission to leave the device. Core strictly decodes the worker envelope,
    revalidates the media signature, recomputes SHA-256, consults the persisted
    privacy policy, invokes a replaceable analyzer, then commits by the CPF CAS.
    """

    MAX_RAW_BYTES = 1024 * 1024
    MAX_BASE64_CHARS = 4 * ((MAX_RAW_BYTES + 2) // 3)
    REMOTE_BLOCKED_SCOPES = frozenset(
        {PrivacyScope.PRIVATE, PrivacyScope.SECRET, PrivacyScope.AUTH}
    )

    def __init__(
        self,
        memory: PerceptualExpressionMemory,
        *,
        analyzer: VisionAnalyzer | None = None,
    ) -> None:
        self.memory = memory
        self.database = memory.database
        self.analyzer = analyzer

    def observe_policy(
        self,
        message: InboundMessage,
        decision: AttentionDecision,
    ) -> None:
        """Persist the latest relevant interaction policy beside the CPF.

        This also refreshes transport metadata such as ``format_type`` so a newer
        observation cannot leave the visual worker with stale operational metadata.
        """

        if decision.action != RoutingAction.RESPOND or not message.expressions:
            return

        scope_id = self.memory.scope_id(message)
        with self.database._lock, self.database._conn:
            for expression in message.expressions:
                key = (
                    message.platform.value,
                    message.account_scope,
                    scope_id,
                    expression.kind.value,
                    expression.native_id,
                )
                row = self.database._conn.execute(
                    """SELECT metadata_json, remote_processing_allowed
                       FROM perceptual_expression_assets
                       WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?""",
                    key,
                ).fetchone()
                if row is None:
                    continue
                try:
                    metadata = dict(json.loads(row["metadata_json"] or "{}"))
                except (TypeError, ValueError, json.JSONDecodeError):
                    metadata = {}
                metadata.update(
                    {
                        "format_type": expression.format_type,
                        "fingerprint_limitation": "transport_metadata_only",
                    }
                )
                remote_allowed = bool(message.remote_processing_allowed)
                was_remote_allowed = bool(row["remote_processing_allowed"])
                clear_policy_defer = remote_allowed and not was_remote_allowed
                self.database._conn.execute(
                    """UPDATE perceptual_expression_assets
                       SET metadata_json=?, remote_processing_allowed=?, privacy_scope=?,
                           visual_defer_until=CASE WHEN ? THEN NULL ELSE visual_defer_until END,
                           visual_last_error_code=CASE
                               WHEN ? AND visual_last_error_code='REMOTE_PROCESSING_DENIED' THEN NULL
                               ELSE visual_last_error_code
                           END
                       WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?""",
                    (
                        json.dumps(metadata, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                        int(remote_allowed),
                        message.privacy_scope.value,
                        int(clear_policy_defer),
                        int(clear_policy_defer),
                        *key,
                    ),
                )

    def claim(
        self,
        *,
        platform: MessagingPlatform,
        account_scope: str,
        limit: int = 1,
        lease_seconds: int = 120,
    ) -> tuple[PerceptualExpressionClaim, ...]:
        return self.memory.claim_visual(
            platform=platform,
            account_scope=account_scope,
            limit=limit,
            lease_seconds=lease_seconds,
        )

    async def ingest_base64(
        self,
        *,
        platform: MessagingPlatform,
        account_scope: str,
        scope_id: str,
        kind: str,
        native_id: str,
        expected_fingerprint: str,
        claim_token: str,
        content_mime: str,
        content_base64: str,
    ) -> VisualEnrichmentResult:
        content = self._decode_content(content_base64)
        mime = self._normalize_and_validate_media(content_mime, content)
        content_sha = hashlib.sha256(content).hexdigest()

        needs_analysis = self.memory.record_visual_content(
            platform=platform,
            account_scope=account_scope,
            scope_id=scope_id,
            kind=kind,
            native_id=native_id,
            claim_token=claim_token,
            expected_fingerprint=expected_fingerprint,
            content_fingerprint=content_sha,
            content_mime=mime,
            content_size_bytes=len(content),
        )

        record = self.memory.get(
            platform=platform,
            account_scope=account_scope,
            scope_id=scope_id,
            kind=kind,
            native_id=native_id,
        )
        if record is None:
            raise VisualEnrichmentError("VISUAL_CPF_NOT_FOUND", "visual CPF disappeared during ingestion")

        if not needs_analysis:
            if (
                record.semantic_status.value == "enriched"
                and record.last_visual_analysis_content_fingerprint == content_sha
            ):
                return VisualEnrichmentResult(
                    status=VisualEnrichmentStatus.REUSED,
                    content_fingerprint=content_sha,
                    semantic_revision=record.semantic_revision,
                    reason_code="CONTENT_SHA_ALREADY_KNOWN",
                )
            raise VisualEnrichmentError(
                "VISUAL_CAS_REJECTED",
                "visual content was rejected by the current CPF claim/revision",
            )

        analyzer = self.analyzer
        if analyzer is None:
            self._defer(
                platform=platform,
                account_scope=account_scope,
                scope_id=scope_id,
                kind=kind,
                native_id=native_id,
                claim_token=claim_token,
                expected_fingerprint=expected_fingerprint,
                error_code="VISUAL_ANALYZER_UNAVAILABLE",
                retry_after_seconds=60,
            )
            return VisualEnrichmentResult(
                status=VisualEnrichmentStatus.DEFERRED,
                content_fingerprint=content_sha,
                semantic_revision=record.semantic_revision,
                reason_code="VISUAL_ANALYZER_UNAVAILABLE",
            )

        if not analyzer.supports_mime(mime):
            self._defer(
                platform=platform,
                account_scope=account_scope,
                scope_id=scope_id,
                kind=kind,
                native_id=native_id,
                claim_token=claim_token,
                expected_fingerprint=expected_fingerprint,
                error_code="UNSUPPORTED_VISUAL_FORMAT",
                retry_after_seconds=3600,
            )
            return VisualEnrichmentResult(
                status=VisualEnrichmentStatus.DEFERRED,
                content_fingerprint=content_sha,
                semantic_revision=record.semantic_revision,
                reason_code="UNSUPPORTED_VISUAL_FORMAT",
            )

        if analyzer.remote_processing and not self._remote_processing_allowed(
            platform=platform,
            account_scope=account_scope,
            scope_id=scope_id,
            kind=kind,
            native_id=native_id,
        ):
            self._defer(
                platform=platform,
                account_scope=account_scope,
                scope_id=scope_id,
                kind=kind,
                native_id=native_id,
                claim_token=claim_token,
                expected_fingerprint=expected_fingerprint,
                error_code="REMOTE_PROCESSING_DENIED",
                retry_after_seconds=86_400,
            )
            return VisualEnrichmentResult(
                status=VisualEnrichmentStatus.DEFERRED,
                content_fingerprint=content_sha,
                semantic_revision=record.semantic_revision,
                reason_code="REMOTE_PROCESSING_DENIED",
            )

        try:
            analysis = await analyzer.analyze(
                content=content,
                mime=mime,
                name=record.name,
                description=record.description,
                tags=tuple(record.tags),
            )
        except Exception as exc:
            self._defer(
                platform=platform,
                account_scope=account_scope,
                scope_id=scope_id,
                kind=kind,
                native_id=native_id,
                claim_token=claim_token,
                expected_fingerprint=expected_fingerprint,
                error_code=f"ANALYZER_{type(exc).__name__.upper()[:64]}",
                retry_after_seconds=60,
            )
            raise VisualEnrichmentError(
                "VISUAL_ANALYZER_FAILED",
                "visual analyzer failed; CPF was deferred for retry",
            ) from exc

        committed = self.memory.apply_visual_enrichment(
            platform=platform,
            account_scope=account_scope,
            scope_id=scope_id,
            kind=kind,
            native_id=native_id,
            profile=analysis.profile,
            expected_fingerprint=expected_fingerprint,
            claim_token=claim_token,
            content_fingerprint=content_sha,
            provider=analysis.provider,
            model=analysis.model,
        )
        if not committed:
            raise VisualEnrichmentError(
                "VISUAL_COMMIT_CAS_REJECTED",
                "visual analysis completed but the CPF changed before commit",
            )

        enriched = self.memory.get(
            platform=platform,
            account_scope=account_scope,
            scope_id=scope_id,
            kind=kind,
            native_id=native_id,
        )
        if enriched is None:
            raise VisualEnrichmentError("VISUAL_CPF_NOT_FOUND", "visual CPF disappeared after commit")
        return VisualEnrichmentResult(
            status=VisualEnrichmentStatus.ENRICHED,
            content_fingerprint=content_sha,
            semantic_revision=enriched.semantic_revision,
            reason_code="VISUAL_ANALYSIS_COMMITTED",
        )

    def _remote_processing_allowed(
        self,
        *,
        platform: MessagingPlatform,
        account_scope: str,
        scope_id: str,
        kind: str,
        native_id: str,
    ) -> bool:
        with self.database._lock:
            row = self.database._conn.execute(
                """SELECT remote_processing_allowed, privacy_scope
                   FROM perceptual_expression_assets
                   WHERE platform=? AND account_scope=? AND scope_id=? AND kind=? AND native_id=?""",
                (platform.value, account_scope, scope_id, kind, native_id),
            ).fetchone()
        if row is None or not bool(row["remote_processing_allowed"]):
            return False
        try:
            scope = PrivacyScope(str(row["privacy_scope"]))
        except ValueError:
            return False
        return scope not in self.REMOTE_BLOCKED_SCOPES

    def _defer(self, **kwargs) -> None:
        if not self.memory.defer_visual(**kwargs):
            raise VisualEnrichmentError(
                "VISUAL_DEFER_CAS_REJECTED",
                "visual job could not be deferred because its claim/revision changed",
            )

    @classmethod
    def _decode_content(cls, encoded: str) -> bytes:
        value = str(encoded or "")
        if not value or len(value) > cls.MAX_BASE64_CHARS:
            raise VisualEnrichmentError("INVALID_VISUAL_PAYLOAD", "visual base64 payload is empty or too large")
        try:
            content = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise VisualEnrichmentError("INVALID_VISUAL_BASE64", "visual payload is not strict base64") from exc
        if not content or len(content) > cls.MAX_RAW_BYTES:
            raise VisualEnrichmentError("INVALID_VISUAL_SIZE", "visual payload is empty or too large")
        return content

    @staticmethod
    def _normalize_and_validate_media(mime: str, content: bytes) -> str:
        normalized = str(mime or "").split(";", 1)[0].strip().lower()
        if normalized == "image/png":
            if not content.startswith(b"\x89PNG\r\n\x1a\n"):
                raise VisualEnrichmentError("INVALID_VISUAL_CONTENT", "image/png payload failed signature validation")
            return normalized
        if normalized == "image/gif":
            if not (content.startswith(b"GIF87a") or content.startswith(b"GIF89a")):
                raise VisualEnrichmentError("INVALID_VISUAL_CONTENT", "image/gif payload failed signature validation")
            return normalized
        if normalized == "image/jpeg":
            if len(content) < 4 or not content.startswith(b"\xff\xd8\xff"):
                raise VisualEnrichmentError("INVALID_VISUAL_CONTENT", "image/jpeg payload failed signature validation")
            return normalized
        if normalized == "image/webp":
            if len(content) < 12 or content[:4] != b"RIFF" or content[8:12] != b"WEBP":
                raise VisualEnrichmentError("INVALID_VISUAL_CONTENT", "image/webp payload failed signature validation")
            return normalized
        if normalized in {"application/json", "text/json"}:
            try:
                root = json.loads(content.decode("utf-8", errors="strict"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise VisualEnrichmentError("INVALID_VISUAL_CONTENT", "Lottie payload is not valid UTF-8 JSON") from exc
            if not isinstance(root, dict) or not isinstance(root.get("layers"), list):
                raise VisualEnrichmentError("INVALID_VISUAL_CONTENT", "Lottie payload failed structural validation")
            for key in ("w", "h", "fr", "ip", "op"):
                if not isinstance(root.get(key), (int, float)):
                    raise VisualEnrichmentError("INVALID_VISUAL_CONTENT", "Lottie payload failed structural validation")
            return "application/json"
        raise VisualEnrichmentError("UNSUPPORTED_VISUAL_MIME", "visual MIME is not allowed by the Core boundary")
