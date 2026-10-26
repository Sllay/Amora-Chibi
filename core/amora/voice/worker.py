"""Standalone renderer-neutral ASGI voice service. Never bootstraps AmoraCore."""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
import logging
import math
import os
import threading
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from pydantic import Field

from amora.observability import JsonFormatter, remember_secret
from amora.security import SecurityMiddleware, SecuritySettings, token_equal
from amora.time import SystemClock
from .audio_contract import validate_wav
from .contract import VoiceRenderRequest, VoiceRenderRequestV2
from .renderers import (
    SupertonicRendererAdapter,
    WorkerVoiceRenderer,
    renderer_from_environment,
)

logger = logging.getLogger(__name__)
RenderRequest = Annotated[
    VoiceRenderRequest | VoiceRenderRequestV2, Field(union_mode="left_to_right")
]


def create_worker(renderer: WorkerVoiceRenderer, service_token, *, render_timeout=None):
    remember_secret(service_token)
    if not service_token:
        raise ValueError("AMORA_VOICE_SERVICE_TOKEN is required")
    if os.getenv("AMORA_MODE") == "cloud" and len(service_token.encode()) < 32:
        raise ValueError("cloud voice service token must contain at least 32 bytes")
    # Compatibility with callers supplying the existing frozen local engine.
    # Do not inspect a lazy renderer's identity property before initialization.
    if not all(hasattr(renderer, name) for name in ("ready", "state", "render")):
        renderer = SupertonicRendererAdapter(renderer)
    timeout = float(
        render_timeout
        if render_timeout is not None
        else os.getenv("AMORA_VOICE_RENDER_TIMEOUT_SECONDS", "60")
    )
    if not math.isfinite(timeout) or not 0 < timeout <= 80:
        raise ValueError("invalid worker render timeout")
    slot = threading.BoundedSemaphore(1)
    tasks = set()

    @asynccontextmanager
    async def lifespan(app):
        try:
            ready = await asyncio.to_thread(renderer.ready)
        except Exception:
            ready = False
        if not ready:
            logger.warning(
                "voice_runtime_unavailable",
                extra={"error_code": "VOICE_RUNTIME_UNAVAILABLE"},
            )
        yield
        # to_thread cannot safely kill a native model call. Keep its slot occupied
        # after timeout/disconnect, discard late output, and bound shutdown wait.
        if tasks:
            await asyncio.wait(tasks, timeout=timeout)

    app = FastAPI(
        title="Amora Voice Worker",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
        lifespan=lifespan,
    )
    app.state.renderer = renderer

    def authenticate(authorization: str | None = Header(default=None)):
        if not authorization or not token_equal(
            authorization, "Bearer " + service_token
        ):
            raise HTTPException(401, "service authentication required")

    @app.get("/live")
    def live():
        return {"status": "alive"}

    @app.get("/ready", dependencies=[Depends(authenticate)])
    def ready():
        try:
            if not renderer.ready():
                raise ValueError("not ready")
            identity = renderer.identity
        except Exception:
            raise HTTPException(503, "VOICE_RUNTIME_UNAVAILABLE") from None
        return {
            "status": "ready",
            "renderer": identity.renderer,
            "voice": identity.voice,
            "model": identity.model,
            "sample_rate": identity.sample_rate,
            "streaming_capable": identity.streaming_capable,
        }

    def render_job(request):
        if not renderer.ready():
            raise RuntimeError("unavailable")
        audio = renderer.render(request.render_input())
        validate_wav(audio.wav_bytes)
        seconds = audio.synthesis_seconds + audio.world_seconds
        if not math.isfinite(seconds) or seconds < 0:
            raise ValueError("invalid metrics")
        return audio, renderer.identity, renderer.state()

    def finished(task):
        tasks.discard(task)
        slot.release()
        # Consume late failure without logging exception strings/reference data.
        if not task.cancelled() and task.exception() is not None:
            logger.warning(
                "voice_render_failed",
                extra={
                    "error_code": "VOICE_RENDER_FAILED",
                    "exception_type": type(task.exception()).__name__,
                },
            )

    @app.post("/v1/render", dependencies=[Depends(authenticate)])
    async def render(request: RenderRequest):
        if not request.text.strip():
            raise HTTPException(422, "invalid request")
        if request.protocol not in renderer.state().get("protocols", []):
            raise HTTPException(409, "VOICE_PROTOCOL_RENDERER_MISMATCH")
        if not slot.acquire(blocking=False):
            raise HTTPException(503, "VOICE_BUSY")
        task = asyncio.create_task(asyncio.to_thread(render_job, request))
        tasks.add(task)
        task.add_done_callback(finished)
        try:
            audio, identity, state = await asyncio.wait_for(
                asyncio.shield(task), timeout
            )
        except TimeoutError:
            logger.warning(
                "voice_render_timeout", extra={"error_code": "VOICE_RENDER_TIMEOUT"}
            )
            raise HTTPException(504, "VOICE_RENDER_TIMEOUT") from None
        except Exception:
            raise HTTPException(503, "VOICE_RENDER_FAILED") from None
        headers = {
            "Cache-Control": "no-store",
            **identity.headers(),
            "X-Amora-Protocol": request.protocol,
            "X-Amora-Render-Seconds": str(
                audio.synthesis_seconds + audio.world_seconds
            ),
            "X-Amora-Unsupported-Controls": ",".join(
                state.get("unsupported_controls", [])
            ),
        }
        if isinstance(request, VoiceRenderRequestV2):
            headers.update(
                {
                    "X-Amora-Request-Id": str(request.identity.request_id),
                    "X-Amora-Utterance-Id": str(request.identity.utterance_id),
                    "X-Amora-Turn-Id": str(request.identity.turn_id)
                    if request.identity.turn_id
                    else "none",
                    "X-Amora-Unsupported-Cues": ",".join(
                        cue
                        for cue in request.cues
                        if cue not in state.get("supported_cues", [])
                    ),
                }
            )
        return Response(audio.wav_bytes, media_type="audio/wav", headers=headers)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    app.add_middleware(
        SecurityMiddleware,
        settings=SecuritySettings.from_environment(worker=True),
        clock=SystemClock(),
        worker=True,
        authenticate=lambda supplied: "service"
        if token_equal(supplied, service_token)
        else None,
    )
    return app


def application():
    return create_worker(
        renderer_from_environment(), os.getenv("AMORA_VOICE_SERVICE_TOKEN", "")
    )


def main():
    # stdout only; no Core paths/database/providers/secrets store are initialized.
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=logging.INFO, handlers=[handler], force=True)
    for name in ("omnivoice", "httpx", "huggingface_hub", "transformers"):
        logging.getLogger(name).setLevel(logging.WARNING)
    import uvicorn

    uvicorn.run(
        "amora.voice.worker:application",
        factory=True,
        host="0.0.0.0",
        port=int(os.getenv("PORT", "8000")),
        workers=1,
        proxy_headers=False,
        access_log=False,
        log_config=None,
        timeout_graceful_shutdown=10,
    )


if __name__ == "__main__":
    main()
