from __future__ import annotations

import asyncio
from pathlib import Path
import os
from typing import Callable
from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.responses import RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from amora.events import PrivacyScope
from amora.voice.backend import VoiceBackend
from amora.voice.cloud import FishGeminiVoiceBackend
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

    The browser remains only UI/player/microphone. Normal Web speech is rendered
    by a Core-owned backend. The default cloud path is Fish S2.1 Pro Free with
    the approved Amora clone; Gemini is the explicit fallback. Supertonic is
    preserved only as an opt-in local/worker backend, never as the Web default.
    """

    auth = [Depends(require_auth)]
    voice_backend = voice or getattr(app.state, "voice_backend", None)
    if voice_backend is None:
        backend_name = os.getenv("AMORA_WEB_VOICE_BACKEND", "cloud").strip().lower()
        if backend_name == "cloud":
            voice_backend = FishGeminiVoiceBackend(core.secrets)
        elif backend_name == "remote":
            from amora.voice.remote import RemoteVoiceBackend
            token = os.getenv("AMORA_VOICE_SERVICE_TOKEN", "")
            if token and token == core.secrets.get("LOCAL_API_TOKEN"):
                raise ValueError("voice service token must differ from the Core user token")
            voice_backend = RemoteVoiceBackend(os.getenv("AMORA_VOICE_WORKER_URL", ""), token)
        elif backend_name == "local":
            voice_backend = AmoraLocalVoiceRenderer(core.paths)
        else:
            raise ValueError("AMORA_WEB_VOICE_BACKEND must be cloud, local or remote")
    # Keep the historical state name for operations/lifespan compatibility while
    # exposing the new generic name for code that no longer assumes local TTS.
    app.state.web_voice = voice_backend
    app.state.web_local_voice = voice_backend

    import threading
    render_slot = threading.BoundedSemaphore(1)

    def render_bounded(item):
        if not render_slot.acquire(blocking=False):
            raise LocalVoiceUnavailable("VOICE_BUSY")
        try:
            return voice_backend.render(item)
        finally:
            render_slot.release()

    def console():
        return getattr(app.state, "server_console", None)

    @app.get("/v1/web/state", dependencies=auth)
    def web_state() -> dict[str, object]:
        voice_state = voice_backend.state()
        candidate = str(voice_state.get("voice") or VOICE_ID)
        return {
            "surface": "AMORA_WEB_SAFARI_LAB_V2",
            "status": "experimental",
            # The Fish clone was approved by BK at provider level; the complete
            # Core -> Safari path still needs physical certification.
            "canonical_voice": False,
            "voice_transport": voice_state.get("provider", "unknown"),
            "voice_candidate": candidate,
            "voice_candidate_available": bool(voice_state.get("available")),
            "voice": voice_state,
            # Backward-compatible alias consumed by the current Web client.
            "local_voice": voice_state,
            "core": core.readiness() if os.getenv("AMORA_MODE") == "cloud" else core.health_report(),
        }

    @app.post("/v1/web/utterance", dependencies=auth)
    async def web_utterance(request: WebTextTurnRequest, http_request: Request) -> dict[str, object]:
        http_request.state.pipeline_stage = "language_social"
        observation = await core.language_social.observe_text(
            request.text,
            speaker_id=request.speaker_id,
            session_id=request.session_id,
            source="web:text",
            explicit_addressee_id="ENTITY_AMORA",
            privacy_scope=request.privacy_scope,
        )
        metadata: dict[str, object] = {
            "speech_device_id": "WEB_SAFARI",
            "explicit_surface_target": "ENTITY_AMORA",
        }
        if request.test_mode:
            metadata.update(
                {
                    "test_mode": True,
                    "memory_consolidation": False,
                    "relationship_learning": False,
                    "emotion_persistence": False,
                }
            )
        http_request.state.pipeline_stage = "conversation"
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

        return {
            "observation": observation.model_dump(mode="json"),
            "turn": turn.model_dump(mode="json"),
            "reply_queued": reply.model_dump(mode="json") if reply else None,
            "test_mode": request.test_mode,
        }

    @app.get("/v1/web/voice/utterances/{utterance_id}/audio", dependencies=auth)
    async def web_voice_audio(utterance_id: UUID) -> Response:
        item = core.conversation.store.get_utterance(utterance_id)
        if item is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="utterance not found")
        if item.device_id != "WEB_SAFARI":
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="utterance does not belong to Safari")
        if item.privacy_scope not in {PrivacyScope.PUBLIC, PrivacyScope.PERSONAL}:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="privacy scope cannot use cloud voice")
        if item.status.value in {"cancelled", "spoken"} or not item.text.strip():
            raise HTTPException(409, "utterance is no longer renderable")

        dashboard = console()
        before_state = voice_backend.state()
        if dashboard is not None:
            primary = str(before_state.get("primary_provider") or before_state.get("provider") or "voz")
            dashboard.set_activity("SINTETIZANDO", f"Amora voz • {primary}", ttl_seconds=60.0)
        try:
            audio = await asyncio.to_thread(render_bounded, item)
        except LocalVoiceUnavailable as exc:
            core.operations.error("voice", "VOICE_RENDER_FAILED", correlation_id=utterance_id)
            if dashboard is not None:
                dashboard.set_module("amora.web_voice", "Amora Voz Web", "OFF", str(exc)[:120])
                dashboard.record_error("Amora Voz Web", str(exc))
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=str(exc),
            ) from exc

        current = core.conversation.store.get_utterance(utterance_id)
        if current is None or current.status.value in {"cancelled", "spoken"}:
            raise HTTPException(409, "utterance is no longer renderable")

        rendered_state = voice_backend.state()
        actual_provider = str(
            rendered_state.get("last_provider")
            or rendered_state.get("provider")
            or "unknown"
        )
        actual_model = str(
            rendered_state.get("last_model")
            or rendered_state.get("model")
            or MODEL_ID
        )
        actual_voice = str(rendered_state.get("voice") or VOICE_ID)
        provider_header = {
            "amora_remote_supertonic_world": "amora-remote",
            "amora_local_supertonic_world": "amora-local",
        }.get(actual_provider, actual_provider)

        if dashboard is not None:
            dashboard.clear_module_override("amora.web_voice")
            dashboard.set_activity(
                "VOZ PRONTA",
                f"{actual_provider} • RTF {audio.total_rtf:.3f}",
                ttl_seconds=4.0,
            )

        headers = {
            "Cache-Control": "no-store",
            "X-Amora-TTS-Provider": provider_header,
            "X-Amora-TTS-Model": actual_model,
            "X-Amora-Voice": actual_voice,
            "X-Amora-Sample-Rate": str(audio.sample_rate),
            "X-Amora-RTF": f"{audio.total_rtf:.6f}",
            "X-Amora-Voice-Certification": "provider-approved-core-safari-gate-pending",
        }
        if actual_provider == "gemini":
            headers["X-Amora-TTS-Fallback"] = "gemini"
        if rendered_state.get("num_steps") is not None:
            headers["X-Amora-TTS-Steps"] = str(rendered_state["num_steps"])
        elif actual_provider in {"amora_local_supertonic_world", "amora_remote_supertonic_world"}:
            headers["X-Amora-TTS-Steps"] = str(NUM_STEPS)

        return Response(
            content=audio.wav_bytes,
            media_type="audio/wav",
            headers=headers,
        )

    web_root = Path(os.environ.get("AMORA_WEB_ROOT", str(Path(__file__).resolve().parents[3] / "web")))
    if not web_root.is_dir():
        return

    @app.get("/web", include_in_schema=False)
    def web_redirect() -> RedirectResponse:
        return RedirectResponse(url="/web/", status_code=307)

    app.mount("/web", StaticFiles(directory=str(web_root), html=True), name="web")
