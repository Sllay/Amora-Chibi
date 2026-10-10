from __future__ import annotations

from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Query

from .models import CognitiveRequest


def register_cognition_routes(app: FastAPI, core, require_auth) -> None:
    auth = [Depends(require_auth)]

    @app.get("/v1/cognition/state", dependencies=auth)
    def cognition_state() -> dict[str, object]:
        return core.cognition.state()

    @app.get("/v1/cognition/history", dependencies=auth)
    def cognition_history(
        limit: int = Query(default=40, ge=1, le=500),
        speaker_id: str | None = Query(default=None),
        turn_id: UUID | None = Query(default=None),
    ) -> list[dict[str, object]]:
        return [
            item.model_dump(mode="json")
            for item in core.cognition.store.list_messages(limit=limit, speaker_id=speaker_id, turn_id=turn_id)
        ]

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
        # Debug/replay surface: generates and journals a plan but deliberately does
        # not bypass ConversationEngine to speak. Normal live speech comes from the
        # STT -> TurnManager -> ConversationEngine path.
        plan = await core.cognition.respond(request)
        await core.cognition.emit_event(plan)
        return plan.model_dump(mode="json")

    @app.post("/v1/cognition/interrupt/{turn_id}", dependencies=auth)
    def cognition_interrupt(turn_id: UUID, reason: str = Query(default="barge_in", max_length=64)) -> dict[str, object]:
        interrupted = core.cognition.interrupt_turn(turn_id, reason=reason)
        if interrupted == 0:
            # Not an error: a turn can already be fully spoken by the time the
            # interrupt signal arrives.
            return {"turn_id": str(turn_id), "interrupted_segments": 0, "already_idle": True}
        return {"turn_id": str(turn_id), "interrupted_segments": interrupted, "already_idle": False}
