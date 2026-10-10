from __future__ import annotations

from uuid import UUID

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.responses import StreamingResponse

from .tts import GeminiStreamingTTS, RemoteVoiceNotAllowed, VoiceEngine, VoiceUnavailable


def register_voice_routes(app: FastAPI, core, require_auth) -> None:
    auth = [Depends(require_auth)]
    engine = VoiceEngine(
        core.conversation.store,
        core.cognition.store,
        GeminiStreamingTTS(core.secrets),
        core.clock,
    )

    @app.get("/v1/voice/state", dependencies=auth)
    def voice_state() -> dict[str, object]:
        return engine.state()

    @app.get("/v1/voice/utterances/{utterance_id}/audio", dependencies=auth)
    async def utterance_audio(utterance_id: UUID):
        try:
            request = engine.prepare(utterance_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="utterance not found") from exc
        except RemoteVoiceNotAllowed as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except VoiceUnavailable as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc

        return StreamingResponse(
            engine.stream(request),
            media_type="audio/L16;codec=pcm;rate=24000",
            headers={
                "Cache-Control": "no-store",
                "X-Amora-TTS-Provider": "gemini",
                "X-Amora-TTS-Model": request.model,
                "X-Amora-Voice": request.voice_id,
                "X-Amora-Sample-Rate": "24000",
            },
        )
