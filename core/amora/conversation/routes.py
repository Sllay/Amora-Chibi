from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel

from amora.app import AmoraCore

from .models import SpeechAck, TurnSignal


class TurnSignalRequest(BaseModel):
    signal: TurnSignal
    allow_backchannel: bool = False


def register_conversation_routes(app: FastAPI, core: AmoraCore, require_auth: Callable[..., None]) -> None:
    @app.get("/v1/conversation/state", dependencies=[Depends(require_auth)])
    def conversation_state() -> dict[str, object]:
        return core.conversation.state()

    @app.post("/v1/conversation/turn", dependencies=[Depends(require_auth)])
    async def decide_turn(request: TurnSignalRequest) -> dict[str, object]:
        if request.allow_backchannel:
            decision, utterance = await core.conversation.enqueue_backchannel(
                request.signal,
                dedupe_key=request.signal.dedupe_key,
            )
            return {
                "decision": decision.model_dump(mode="json"),
                "utterance": utterance.model_dump(mode="json") if utterance else None,
            }
        decision = core.conversation.decide_turn(request.signal)
        return {"decision": decision.model_dump(mode="json"), "utterance": None}

    @app.get("/v1/conversation/next", dependencies=[Depends(require_auth)])
    def next_utterance(device_id: str = Query(default="DEVICE_A15", min_length=1, max_length=128)) -> dict[str, object]:
        item = core.conversation.claim_next(device_id)
        return {"utterance": item.model_dump(mode="json") if item else None}

    @app.post("/v1/conversation/utterances/{utterance_id}/ack", dependencies=[Depends(require_auth)])
    async def acknowledge_utterance(utterance_id: str, ack: SpeechAck) -> dict[str, object]:
        item = await core.conversation.acknowledge(utterance_id, ack)
        if item is None:
            raise HTTPException(status_code=404, detail="utterance not found")
        return {"utterance": item.model_dump(mode="json")}

    @app.get("/v1/conversation/turns", dependencies=[Depends(require_auth)])
    def list_turns(limit: int = Query(default=20, ge=1, le=200)) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in core.conversation.store.list_turns(limit=limit)]

    @app.get("/v1/conversation/utterances", dependencies=[Depends(require_auth)])
    def list_utterances(limit: int = Query(default=20, ge=1, le=200)) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in core.conversation.store.list_utterances(limit=limit)]
