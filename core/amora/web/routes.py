from __future__ import annotations

import asyncio
from pathlib import Path
import os
from typing import Callable
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from amora.events import PrivacyScope
from amora.voice.backend import VoiceBackend
from amora.voice.local_amora import AmoraLocalVoiceRenderer, LocalVoiceUnavailable, MODEL_ID, NUM_STEPS, VOICE_ID


class WebTextTurnRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    speaker_id: str = "PERSON_BK"
    session_id: str | None = None
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    remote_processing_allowed: bool = False
    test_mode: bool = True


def register_web_routes(app: FastAPI, core, require_auth: Callable[..., None], *, voice: VoiceBackend | None = None) -> None:
    """Register the Safari/Web platform adapter without duplicating Core logic.

    The web client reuses the same language/social, conversation, STT and speech
    queue contracts as Android. Browser playback is only a platform adapter: the
    voice itself is rendered locally by the Termux Core with Amora's frozen
    Supertonic 60/40 + FINAL_B + VoiceSignature V1 + bounded Emotion V2 stack.
    """

    auth = [Depends(require_auth)]
    local_voice = voice or getattr(app.state, "voice_backend", None)
    if local_voice is None:
        backend_name = os.getenv("AMORA_VOICE_BACKEND", "local")
        if backend_name == "remote":
            from amora.voice.remote import RemoteVoiceBackend
            token = os.getenv("AMORA_VOICE_SERVICE_TOKEN", "")
            if token and token == core.secrets.get("LOCAL_API_TOKEN"):
                raise ValueError("voice service token must differ from the Core user token")
            local_voice = RemoteVoiceBackend(os.getenv("AMORA_VOICE_WORKER_URL", ""), token)
        elif backend_name == "local":
            local_voice = AmoraLocalVoiceRenderer(core.paths)
        else:
            raise ValueError("AMORA_VOICE_BACKEND must be local or remote")
    app.state.web_local_voice = local_voice
    import threading
    render_slot = threading.BoundedSemaphore(1)

    def render_bounded(item):
        if not render_slot.acquire(blocking=False):
            raise LocalVoiceUnavailable("VOICE_BUSY")
        try:
            return local_voice.render(item)
        finally:
            render_slot.release()

    def voice_state_remote():
        return local_voice.state().get("provider") == "amora_remote_supertonic_world"

    def console():
        return getattr(app.state, "server_console", None)

    @app.get("/v1/web/state", dependencies=auth)
    def web_state() -> dict[str, object]:
        voice_state = local_voice.state()
        return {
            "surface": "AMORA_WEB_SAFARI_LAB_V2",
            "status": "experimental",
            # Identity is approved in the lab; physical/canonical product
            # certification is still a separate gate.
            "canonical_voice": False,
            "voice_transport": voice_state["provider"],
            "voice_candidate": VOICE_ID,
            "voice_candidate_available": voice_state["available"],
            "local_voice": voice_state,
            "core": core.readiness() if os.getenv("AMORA_MODE") == "cloud" else core.health_report(),
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

    @app.get("/v1/web/voice/utterances/{utterance_id}/audio", dependencies=auth)
    async def web_local_voice_audio(utterance_id: UUID) -> Response:
        item = core.conversation.store.get_utterance(utterance_id)
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="utterance not found")
        if item.device_id != "WEB_SAFARI":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="utterance does not belong to Safari")
        if item.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="privacy scope cannot be rendered")

        dashboard = console()
        if dashboard is not None:
            dashboard.set_activity("SINTETIZANDO", "Amora local • Supertonic → FINAL_B", ttl_seconds=60.0)
        try:
            audio = await asyncio.to_thread(render_bounded, item)
        except LocalVoiceUnavailable as exc:
            core.operations.error("voice", "VOICE_RENDER_FAILED", correlation_id=utterance_id)
            if dashboard is not None:
                dashboard.set_module("amora.local_voice", "Amora Voz Local", "OFF", str(exc)[:120])
                dashboard.record_error("Amora Voz Local", str(exc))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

        current = core.conversation.store.get_utterance(utterance_id)
        if current is None or current.status.value in {"cancelled", "spoken"}:
            raise HTTPException(409, "utterance is no longer renderable")

        if dashboard is not None:
            dashboard.clear_module_override("amora.local_voice")
            dashboard.set_activity(
                "VOZ PRONTA",
                f"FINAL_B + Signature V1 • RTF {audio.total_rtf:.3f}",
                ttl_seconds=4.0,
            )
        return Response(
            content=audio.wav_bytes,
            media_type="audio/wav",
            headers={
                "Cache-Control": "no-store",
                "X-Amora-TTS-Provider": "amora-remote" if voice_state_remote() else "amora-local",
                "X-Amora-TTS-Model": MODEL_ID,
                "X-Amora-Voice": VOICE_ID,
                "X-Amora-Sample-Rate": str(audio.sample_rate),
                "X-Amora-TTS-Steps": str(NUM_STEPS),
                "X-Amora-RTF": f"{audio.total_rtf:.6f}",
                "X-Amora-Voice-Certification": "lab-approved-physical-gate-pending",
            },
        )

    web_root = Path(os.environ.get("AMORA_WEB_ROOT", str(Path(__file__).resolve().parents[3] / "web")))
    if not web_root.is_dir():
        return

    @app.get("/web", include_in_schema=False)
    def web_redirect() -> RedirectResponse:
        return RedirectResponse(url="/web/", status_code=307)

    app.mount("/web", StaticFiles(directory=str(web_root), html=True), name="web")
