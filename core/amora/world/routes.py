from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, status

from amora.app import AmoraCore

from .models import WorldClaimDraft


def register_world_routes(
    app: FastAPI,
    core: AmoraCore,
    require_auth: Callable[..., None],
) -> None:
    protected = [Depends(require_auth)]

    @app.post("/v1/world/record", dependencies=protected)
    def record_world_claim(draft: WorldClaimDraft) -> dict[str, object]:
        try:
            return core.world.record(draft).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc

    @app.get("/v1/world/state", dependencies=protected)
    def current_world_state(subject_id: str | None = Query(default=None)) -> list[dict[str, object]]:
        return [claim.model_dump(mode="json") for claim in core.world.current_state(subject_id=subject_id)]

    @app.get("/v1/world/history", dependencies=protected)
    def world_history(
        subject_id: str | None = Query(default=None),
        predicate: str | None = Query(default=None),
        limit: int = Query(default=100, ge=1, le=500),
    ) -> list[dict[str, object]]:
        return [
            claim.model_dump(mode="json")
            for claim in core.world.history(subject_id=subject_id, predicate=predicate, limit=limit)
        ]

    @app.post("/v1/world/{claim_id}/promote", dependencies=protected)
    def promote_world_claim(claim_id: UUID, confidence: float = Query(ge=0.0, le=1.0)) -> dict[str, object]:
        try:
            return core.world.promote_to_belief(str(claim_id), confidence=confidence).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="claim not found") from exc

    @app.get("/v1/self", dependencies=protected)
    def self_snapshot() -> dict[str, dict[str, object]]:
        return core.self_model.snapshot()

    @app.get("/v1/relationships/history", dependencies=protected)
    def relationship_history(
        source_id: str = Query(..., min_length=1),
        target_id: str | None = Query(default=None),
    ) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in core.relationships.history(source_id, target_id=target_id)]
