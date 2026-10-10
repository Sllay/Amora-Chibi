from __future__ import annotations

from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field

from .models import CognitiveRequest


class ReplayRequest(BaseModel):
    provider_names: list[str] = Field(default_factory=list, max_length=8)


def register_cognition_routes(app: FastAPI, core, require_auth) -> None:
    auth = [Depends(require_auth)]

    @app.get("/v1/cognition/state", dependencies=auth)
    def cognition_state() -> dict[str, object]:
        return core.cognition.state()

    @app.get("/v1/cognition/usage", dependencies=auth)
    def cognition_usage() -> list[dict[str, object]]:
        return core.cognition.store.usage_ledger()

    @app.get("/v1/cognition/history", dependencies=auth)
    def cognition_history(
        limit: int = Query(default=40, ge=1, le=500),
        speaker_id: str | None = Query(default=None),
        turn_id: UUID | None = Query(default=None),
    ) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in core.cognition.store.list_messages(limit=limit, speaker_id=speaker_id, turn_id=turn_id)]

    @app.get("/v1/cognition/journal", dependencies=auth)
    def cognition_journal(limit: int = Query(default=100, ge=1, le=1000)) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in core.cognition.store.recent_journal(limit=limit)]

    @app.get("/v1/cognition/journal/{turn_id}", dependencies=auth)
    def cognition_turn_journal(turn_id: UUID) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in core.cognition.store.journal_for_turn(turn_id)]

    @app.get("/v1/cognition/segments/{turn_id}", dependencies=auth)
    def cognition_segments(turn_id: UUID) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in core.cognition.store.list_segments(turn_id)]

    @app.post("/v1/cognition/respond", dependencies=auth)
    async def cognition_respond(request: CognitiveRequest) -> dict[str, object]:
        plan = await core.cognition.respond(request)
        await core.cognition.emit_event(plan)
        return plan.model_dump(mode="json")

    @app.post("/v1/cognition/replay/{turn_id}", dependencies=auth)
    async def cognition_replay(turn_id: UUID, request: ReplayRequest) -> dict[str, object]:
        try:
            results = await core.cognition.replay(
                turn_id,
                provider_names=request.provider_names or None,
            )
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return {
            "turn_id": str(turn_id),
            "sandbox": True,
            "state_mutation_allowed": False,
            "results": [item.model_dump(mode="json") for item in results],
        }

    @app.post("/v1/cognition/interrupt/{turn_id}", dependencies=auth)
    def cognition_interrupt(turn_id: UUID, reason: str = Query(default="barge_in", max_length=64)) -> dict[str, object]:
        interrupted_segments = core.cognition.interrupt_turn(turn_id, reason=reason)
        cancelled_utterances = core.conversation.store.cancel_cognitive_turn(turn_id, reason=reason)
        return {
            "turn_id": str(turn_id),
            "interrupted_segments": interrupted_segments,
            "cancelled_utterances": cancelled_utterances,
            "already_idle": interrupted_segments == 0 and cancelled_utterances == 0,
        }

    # Voice is part of the V1.0 cognitive delivery path. Registering it here keeps
    # API assembly modular without making the top-level API module own provider code.
    from amora.voice.routes import register_voice_routes
    register_voice_routes(app, core, require_auth)
