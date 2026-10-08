from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, status

from amora.app import AmoraCore

from .models import AppraisalInput


def register_emotion_routes(
    app: FastAPI,
    core: AmoraCore,
    require_auth: Callable[..., None],
) -> None:
    protected = [Depends(require_auth)]

    @app.post("/v1/emotion/appraise", dependencies=protected)
    async def appraise(item: AppraisalInput) -> dict[str, object]:
        try:
            result = await core.emotion.process(item)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    @app.get("/v1/emotion/state", dependencies=protected)
    def state() -> dict[str, object]:
        return {
            "mood": core.emotion.current_mood().model_dump(mode="json"),
            "affect": [
                core.emotion.affect_for(item.entity_id).model_dump(mode="json")
                for item in core.emotion.store.list_affect()
            ],
            "pending_bids": [item.model_dump(mode="json") for item in core.emotion.pending_bids()],
        }

    @app.get("/v1/emotion/affect/{entity_id}", dependencies=protected)
    def affect(entity_id: str) -> dict[str, object]:
        try:
            return core.emotion.affect_for(entity_id).model_dump(mode="json")
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @app.get("/v1/emotion/events", dependencies=protected)
    def events(
        target_id: str | None = Query(default=None),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in core.emotion.store.list_events(target_id=target_id, limit=limit)]

    @app.get("/v1/emotion/bids", dependencies=protected)
    def bids(limit: int = Query(default=50, ge=1, le=200)) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in core.emotion.pending_bids(limit=limit)]

    @app.post("/v1/emotion/bids/{bid_id}/consume", dependencies=protected)
    def consume_bid(bid_id: UUID) -> dict[str, object]:
        consumed = core.emotion.consume_bid(str(bid_id))
        if not consumed:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="social bid not found")
        return {"bid_id": str(bid_id), "consumed": True}
