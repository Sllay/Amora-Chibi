from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field

from amora.app import AmoraCore
from amora.events import PrivacyScope

from .models import SpeechAck, TurnSignal


class TurnSignalRequest(BaseModel):
    signal: TurnSignal
    allow_backchannel: bool = False


class CancelSpeechRequest(BaseModel):
    reason: str = Field(default="barge_in", min_length=1, max_length=96)
    spoken_chars: int | None = Field(default=None, ge=0, le=12000)
    speech_only: bool = False


class TechnicalTestTurnRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    speaker_id: str = "PERSON_BK"
    addressee_id: str = "ENTITY_AMORA"
    remote_processing_allowed: bool = False
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL


def register_conversation_routes(app: FastAPI, core: AmoraCore, require_auth: Callable[..., None]) -> None:
    def console():
        return getattr(app.state, "server_console", None)

    def delivery_surface(item) -> tuple[str, str, str]:
        if item.device_id == "WEB_SAFARI":
            return "web.voice", "Web Voice", "Safari"
        return "android.tts", "Android TTS", "Android"

    @app.get("/v1/conversation/state", dependencies=[Depends(require_auth)])
    def conversation_state() -> dict[str, object]:
        return core.conversation.state()

    @app.post("/v1/conversation/test-turn", dependencies=[Depends(require_auth)])
    async def technical_test_turn(request: TechnicalTestTurnRequest) -> dict[str, object]:
        """Exercise dialogue/cognition/voice queue without persistent learning."""
        decision, utterance = await core.conversation.observe_completed_utterance(
            request.text,
            speaker_id=request.speaker_id,
            addressee_id=request.addressee_id,
            source_event_id=None,
            privacy_scope=request.privacy_scope,
            remote_processing_allowed=request.remote_processing_allowed,
            metadata={
                "test_mode": True,
                "memory_consolidation": False,
                "relationship_learning": False,
                "emotion_persistence": False,
            },
        )
        return {
            "test_mode": True,
            "decision": decision.model_dump(mode="json"),
            "utterance": utterance.model_dump(mode="json") if utterance else None,
            "conversation_state": core.conversation.state(),
        }

    @app.post("/v1/conversation/turn", dependencies=[Depends(require_auth)])
    async def decide_turn(request: TurnSignalRequest) -> dict[str, object]:
        if request.allow_backchannel:
            decision, utterance = await core.conversation.enqueue_backchannel(request.signal, dedupe_key=request.signal.dedupe_key)
            return {"decision": decision.model_dump(mode="json"), "utterance": utterance.model_dump(mode="json") if utterance else None}
        decision = core.conversation.decide_turn(request.signal)
        return {"decision": decision.model_dump(mode="json"), "utterance": None}

    @app.get("/v1/conversation/next", dependencies=[Depends(require_auth)])
    def next_utterance(
        device_id: str = Query(default="DEVICE_A15", min_length=1, max_length=128),
        cognitive_turn_id: str | None = Query(default=None, min_length=1, max_length=128),
    ) -> dict[str, object]:
        item = core.conversation.claim_next(device_id, cognitive_turn_id=cognitive_turn_id)
        return {"utterance": item.model_dump(mode="json") if item else None}

    @app.post("/v1/conversation/utterances/{utterance_id}/started", dependencies=[Depends(require_auth)])
    def utterance_started(utterance_id: str) -> dict[str, object]:
        item = core.conversation.mark_utterance_speaking(utterance_id)
        if item is None:
            raise HTTPException(status_code=404, detail="utterance not found")
        dashboard = console()
        if dashboard is not None:
            module_id, module_name, surface = delivery_surface(item)
            dashboard.set_module(module_id, module_name, "ON", "fala iniciada")
            dashboard.set_activity("FALANDO", f"{surface} • {item.text[:110]}", ttl_seconds=2.0)
        return {"utterance": item.model_dump(mode="json")}

    @app.post("/v1/conversation/utterances/{utterance_id}/ack", dependencies=[Depends(require_auth)])
    async def acknowledge_utterance(utterance_id: str, ack: SpeechAck) -> dict[str, object]:
        dashboard = console()
        if ack.spoken:
            item = await core.conversation.acknowledge(utterance_id, ack)
            if item is None:
                raise HTTPException(status_code=404, detail="utterance not found")
            if dashboard is not None:
                module_id, module_name, surface = delivery_surface(item)
                dashboard.set_module(module_id, module_name, "ON", "fala concluída")
                dashboard.set_activity("IDLE", f"fala concluída no {surface}", ttl_seconds=1.5)
            return {"utterance": item.model_dump(mode="json")}

        reason = (ack.reason or "tts_delivery_failed")[:96]
        item, cancelled = await core.conversation.cancel_utterance(
            utterance_id,
            reason=reason,
            spoken_chars=None,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="utterance not found")
        if dashboard is not None:
            module_id, module_name, surface = delivery_surface(item)
            dashboard.set_module(module_id, module_name, "DEGRADED", reason)
            dashboard.set_activity("FALHA DE VOZ", f"{surface} não confirmou a fala", ttl_seconds=8.0)
            dashboard.record_error(module_name, f"{reason}; {cancelled} fala(s) cancelada(s)")
        return {
            "utterance": item.model_dump(mode="json"),
            "cancelled_utterances": cancelled,
            "delivery_failed": True,
        }

    @app.post("/v1/conversation/utterances/{utterance_id}/cancel", dependencies=[Depends(require_auth)])
    async def cancel_utterance(utterance_id: str, request: CancelSpeechRequest) -> dict[str, object]:
        item, cancelled = await core.conversation.cancel_utterance(
            utterance_id,
            reason=request.reason,
            spoken_chars=request.spoken_chars,
            speech_only=request.speech_only,
        )
        if item is None:
            raise HTTPException(status_code=404, detail="utterance not found")
        return {
            "utterance": item.model_dump(mode="json"),
            "cancelled_utterances": cancelled,
            "spoken_progress_known": request.spoken_chars is not None,
        }

    @app.get("/v1/conversation/turns", dependencies=[Depends(require_auth)])
    def list_turns(limit: int = Query(default=20, ge=1, le=200)) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in core.conversation.store.list_turns(limit=limit)]

    @app.get("/v1/conversation/utterances", dependencies=[Depends(require_auth)])
    def list_utterances(limit: int = Query(default=20, ge=1, le=200)) -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in core.conversation.store.list_utterances(limit=limit)]
