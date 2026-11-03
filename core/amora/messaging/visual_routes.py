from __future__ import annotations

from fastapi import Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from .models import MessagingPlatform
from .visual_enrichment import VisualEnrichmentError


class VisualAssetKey(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: MessagingPlatform
    account_scope: str = Field(default="primary", min_length=1, max_length=96)
    scope_id: str = Field(min_length=1, max_length=512)
    kind: str = Field(min_length=1, max_length=64)
    native_id: str = Field(min_length=1, max_length=256)
    expected_fingerprint: str = Field(min_length=64, max_length=64)
    claim_token: str = Field(min_length=16, max_length=128)


class VisualContentRequest(VisualAssetKey):
    content_mime: str = Field(min_length=3, max_length=100)
    content_base64: str = Field(min_length=4, max_length=1_398_104)


class VisualDeferRequest(VisualAssetKey):
    error_code: str = Field(min_length=1, max_length=96)
    retry_after_seconds: int = Field(default=60, ge=1, le=86_400)


def register_visual_enrichment_routes(app, core, require_auth) -> None:
    """Register the local authenticated worker boundary for one-shot asset vision."""

    @app.get("/v1/messaging/worker/visual", dependencies=[Depends(require_auth)])
    def visual_claim(
        platform: MessagingPlatform,
        account_scope: str = Query(default="primary", min_length=1, max_length=96),
        limit: int = Query(default=1, ge=1, le=32),
        lease_seconds: int = Query(default=120, ge=5, le=900),
    ):
        claims = core.messaging.visual_enrichment.claim(
            platform=platform,
            account_scope=account_scope,
            limit=limit,
            lease_seconds=lease_seconds,
        )
        return {
            "work": [
                {
                    "platform": claim.record.platform.value,
                    "account_scope": claim.record.account_scope,
                    "scope_id": claim.record.scope_id,
                    "kind": claim.record.kind,
                    "native_id": claim.record.native_id,
                    "name": claim.record.name,
                    "description": claim.record.description,
                    "tags": list(claim.record.tags),
                    "metadata": dict(claim.record.metadata),
                    "expected_fingerprint": claim.record.fingerprint,
                    "claim_token": claim.claim_token,
                    "claimed_until": claim.claimed_until.isoformat(),
                }
                for claim in claims
            ]
        }

    @app.post("/v1/messaging/worker/visual/content", dependencies=[Depends(require_auth)])
    async def visual_content(payload: VisualContentRequest):
        try:
            result = await core.messaging.visual_enrichment.ingest_base64(
                platform=payload.platform,
                account_scope=payload.account_scope,
                scope_id=payload.scope_id,
                kind=payload.kind,
                native_id=payload.native_id,
                expected_fingerprint=payload.expected_fingerprint,
                claim_token=payload.claim_token,
                content_mime=payload.content_mime,
                content_base64=payload.content_base64,
            )
        except VisualEnrichmentError as exc:
            if exc.code in {
                "VISUAL_CAS_REJECTED",
                "VISUAL_COMMIT_CAS_REJECTED",
                "VISUAL_DEFER_CAS_REJECTED",
                "VISUAL_CPF_NOT_FOUND",
            }:
                raise HTTPException(
                    status_code=status.HTTP_409_CONFLICT,
                    detail={"code": exc.code, "message": str(exc)},
                ) from exc
            if exc.code in {
                "INVALID_VISUAL_PAYLOAD",
                "INVALID_VISUAL_BASE64",
                "INVALID_VISUAL_SIZE",
                "INVALID_VISUAL_CONTENT",
                "UNSUPPORTED_VISUAL_MIME",
            }:
                raise HTTPException(
                    status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                    detail={"code": exc.code, "message": str(exc)},
                ) from exc
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={"code": exc.code, "message": str(exc)},
            ) from exc
        return result.model_dump(mode="json")

    @app.post("/v1/messaging/worker/visual/defer", dependencies=[Depends(require_auth)])
    def visual_defer(payload: VisualDeferRequest):
        accepted = core.messaging.expression_memory.defer_visual(
            platform=payload.platform,
            account_scope=payload.account_scope,
            scope_id=payload.scope_id,
            kind=payload.kind,
            native_id=payload.native_id,
            claim_token=payload.claim_token,
            expected_fingerprint=payload.expected_fingerprint,
            error_code=payload.error_code,
            retry_after_seconds=payload.retry_after_seconds,
        )
        if not accepted:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "VISUAL_DEFER_CAS_REJECTED", "message": "visual claim/revision is no longer current"},
            )
        return {"status": "deferred"}
