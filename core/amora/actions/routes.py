from __future__ import annotations

from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, status
from pydantic import BaseModel, Field

from amora.app import AmoraCore


class PlanRequest(BaseModel):
    dedupe_key: str | None = Field(default=None, max_length=256)


class ExecuteRequest(BaseModel):
    plan_id: str | None = None
    dedupe_key: str | None = Field(default=None, max_length=256)


def register_action_routes(app: FastAPI, core: AmoraCore, require_auth: Callable[[], None]) -> None:
    auth = [Depends(require_auth)]

    @app.get("/v1/actions/state", dependencies=auth)
    def action_state() -> dict[str, object]:
        return core.actions.state()

    @app.get("/v1/actions/plans", dependencies=auth)
    def action_plans(limit: int = 20) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in core.actions.store.list_plans(limit=limit)]

    @app.get("/v1/actions/executions", dependencies=auth)
    def action_executions(limit: int = 50) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in core.actions.store.list_executions(limit=limit)]

    @app.post("/v1/actions/plan", dependencies=auth)
    async def action_plan(request: PlanRequest) -> dict[str, object]:
        plan = await core.actions.plan_current(dedupe_key=request.dedupe_key)
        if plan is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="no active intention or no supported planner/skill",
            )
        return plan.model_dump(mode="json")

    @app.post("/v1/actions/execute", dependencies=auth)
    async def action_execute(request: ExecuteRequest) -> dict[str, object]:
        try:
            if request.plan_id:
                result = await core.actions.execute_plan(request.plan_id, dedupe_key=request.dedupe_key)
            else:
                result = await core.actions.execute_current(dedupe_key=request.dedupe_key)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        if result is None:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="no active intention or no supported planner/skill",
            )
        return result.model_dump(mode="json")
