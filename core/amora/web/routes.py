from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import Depends, FastAPI
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from amora.events import PrivacyScope


class WebTextTurnRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    speaker_id: str = "PERSON_BK"
    session_id: str | None = None
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    remote_processing_allowed: bool = False
    test_mode: bool = True


def register_web_routes(app: FastAPI, core, require_auth: Callable[..., None]) -> None:
    """Register the Safari/Web platform adapter without duplicating Core logic.

    The web client reuses the same language/social, conversation, STT and speech
    queue contracts as Android. The browser-specific surface is intentionally a
    replaceable adapter; it does not own Amora's identity or canonical state.
    """

    auth = [Depends(require_auth)]

    @app.get("/v1/web/state", dependencies=auth)
    def web_state() -> dict[str, object]:
        return {
            "surface": "AMORA_WEB_SAFARI_LAB_V1",
            "status": "experimental",
            "canonical_voice": False,
            "voice_transport": "browser_speech_synthesis_debug",
            "core": core.health_report(),
        }

    @app.post("/v1/web/utterance", dependencies=auth)
    async def web_utterance(request: WebTextTurnRequest) -> dict[str, object]:
        observation = await core.language_social.observe_text(
            request.text,
            speaker_id=request.speaker_id,
            session_id=request.session_id,
            source="web:text",
            privacy_scope=request.privacy_scope,
        )
        metadata: dict[str, object] = {"speech_device_id": "WEB_SAFARI"}
        if request.test_mode:
            metadata.update(
                {
                    "test_mode": True,
                    "memory_consolidation": False,
                    "relationship_learning": False,
                    "emotion_persistence": False,
                }
            )
        try:
            turn, reply = await core.conversation.observe_completed_utterance(
                request.text,
                speaker_id=request.speaker_id,
                addressee_id=observation.addressee.entity_id,
                source_event_id=observation.event_id,
                privacy_scope=request.privacy_scope,
                remote_processing_allowed=request.remote_processing_allowed,
                session_id=request.session_id,
                metadata=metadata,
            )
        finally:
            core.social.end_turn(request.speaker_id)

        return {
            "observation": observation.model_dump(mode="json"),
            "turn": turn.model_dump(mode="json"),
            "reply_queued": reply.model_dump(mode="json") if reply else None,
            "test_mode": request.test_mode,
        }

    web_root = Path(__file__).resolve().parents[3] / "web"
    if not web_root.is_dir():
        return

    @app.get("/web", include_in_schema=False)
    def web_redirect() -> RedirectResponse:
        return RedirectResponse(url="/web/", status_code=307)

    app.mount("/web", StaticFiles(directory=str(web_root), html=True), name="web")
