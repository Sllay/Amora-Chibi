from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

from amora.app import AmoraCore

from .models import DecisionContext, DriveType


class CycleRequest(BaseModel):
    context: DecisionContext = Field(default_factory=DecisionContext)
    dedupe_key: str | None = Field(default=None, max_length=256)


class DriveAdjustmentRequest(BaseModel):
    delta: float = Field(ge=-1.0, le=1.0)
    reason: str = Field(min_length=1, max_length=300)
    source_event_id: UUID | None = None
    dedupe_key: str | None = Field(default=None, max_length=256)


def register_autonomy_routes(
    app: FastAPI,
    core: AmoraCore,
    require_auth: Callable[..., None],
) -> None:
    protected = [Depends(require_auth)]

    @app.get("/v1/autonomy/state", dependencies=protected)
    def autonomy_state() -> dict[str, object]:
        return core.autonomy.state()

    @app.post("/v1/autonomy/cycle", dependencies=protected)
    async def autonomy_cycle(item: CycleRequest) -> dict[str, object]:
        result = await core.autonomy.cycle(item.context, dedupe_key=item.dedupe_key)
        return result.model_dump(mode="json")

    @app.post("/v1/autonomy/drives/{drive}/adjust", dependencies=protected)
    def adjust_drive(drive: DriveType, item: DriveAdjustmentRequest) -> dict[str, object]:
        try:
            state, deduplicated = core.autonomy.adjust_drive(
                drive,
                item.delta,
                reason=item.reason,
                source_event_id=item.source_event_id,
                dedupe_key=item.dedupe_key,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
        return {"drive": state.model_dump(mode="json"), "deduplicated": deduplicated}

    @app.get("/v1/autonomy/decisions", dependencies=protected)
    def decisions(limit: int = Query(default=50, ge=1, le=200)) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in core.autonomy.store.list_decisions(limit=limit)]
