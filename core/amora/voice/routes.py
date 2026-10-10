from __future__ import annotations

import math
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

    def console():
        return getattr(app.state, "server_console", None)

    def unavailable(exc: VoiceUnavailable) -> HTTPException:
        retry = exc.retry_after_seconds
        dashboard = console()
        headers = None
        if retry is not None and retry > 0:
            seconds = max(1, math.ceil(retry))
            headers = {"Retry-After": str(seconds)}
            if dashboard is not None:
                dashboard.set_module("gemini.tts", "Gemini Voz", "WAIT", f"quota; retry em ~{seconds}s")
                dashboard.set_activity("VOZ LOCAL", "Gemini TTS em cooldown", ttl_seconds=3.0)
        elif dashboard is not None:
            dashboard.set_module("gemini.tts", "Gemini Voz", "OFF", str(exc))
        return HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=str(exc),
            headers=headers,
        )

    @app.get("/v1/voice/state", dependencies=auth)
    def voice_state() -> dict[str, object]:
        state = engine.state()
        dashboard = console()
        if dashboard is not None:
            cooldown = int(state.get("expressive_tts_cooldown_seconds") or 0)
            if cooldown > 0:
                dashboard.set_module("gemini.tts", "Gemini Voz", "WAIT", f"cooldown {cooldown}s")
            elif state.get("expressive_tts_available"):
                dashboard.clear_module_override("gemini.tts")
        return state

    @app.get("/v1/voice/utterances/{utterance_id}/audio", dependencies=auth)
    async def utterance_audio(utterance_id: UUID):
        dashboard = console()
        try:
            request = engine.prepare(utterance_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="utterance not found") from exc
        except RemoteVoiceNotAllowed as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except VoiceUnavailable as exc:
            raise unavailable(exc) from exc

        if dashboard is not None:
            dashboard.set_activity("SINTETIZANDO", "Gemini TTS", ttl_seconds=45.0)

        # Prime the async generator before StreamingResponse commits HTTP 200.
        # Provider 429/credentials errors therefore become a normal 503 that the
        # Android client can immediately route to local TextToSpeech instead of
        # seeing a broken 200 stream and retrying the same utterance forever.
        stream = engine.stream(request).__aiter__()
        try:
            first_chunk = await anext(stream)
        except StopAsyncIteration as exc:
            empty = VoiceUnavailable("Gemini TTS returned no audio")
            if dashboard is not None:
                dashboard.set_module("gemini.tts", "Gemini Voz", "DEGRADED", "resposta sem áudio")
            raise unavailable(empty) from exc
        except VoiceUnavailable as exc:
            raise unavailable(exc) from exc
        except Exception as exc:
            if dashboard is not None:
                dashboard.set_module("gemini.tts", "Gemini Voz", "DEGRADED", type(exc).__name__)
                dashboard.record_error("Gemini Voz", f"{type(exc).__name__}: {exc}")
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="remote TTS failed") from exc

        if dashboard is not None:
            dashboard.clear_module_override("gemini.tts")
            dashboard.set_activity("FALANDO", f"Gemini • {request.voice_id}", ttl_seconds=60.0)

        async def body():
            try:
                yield first_chunk
                async for chunk in stream:
                    yield chunk
            except Exception as exc:
                if dashboard is not None:
                    dashboard.set_module("gemini.tts", "Gemini Voz", "DEGRADED", type(exc).__name__)
                    dashboard.record_error("Gemini Voz", f"stream interrompido: {type(exc).__name__}: {exc}")
                raise
            finally:
                if dashboard is not None:
                    dashboard.set_activity("IDLE", "fala entregue", ttl_seconds=1.5)

        return StreamingResponse(
            body(),
            media_type="audio/L16;codec=pcm;rate=24000",
            headers={
                "Cache-Control": "no-store",
                "X-Amora-TTS-Provider": "gemini",
                "X-Amora-TTS-Model": request.model,
                "X-Amora-Voice": request.voice_id,
                "X-Amora-Sample-Rate": "24000",
            },
        )
