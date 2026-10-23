"""Standalone ASGI voice worker. Does not bootstrap or import AmoraCore."""
from amora.security import SecurityMiddleware, SecuritySettings, token_equal
from amora.time import SystemClock
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response, JSONResponse
from fastapi.exceptions import RequestValidationError
from .contract import VoiceRenderRequest
from .local_amora import MODEL_ID, VOICE_ID
from .remote import validate_wav


def create_worker(renderer, service_token):
    from amora.observability import remember_secret
    remember_secret(service_token)
    import os
    if os.getenv("AMORA_MODE") == "cloud" and len(service_token.encode()) < 32:
        raise ValueError("cloud voice service token must contain at least 32 bytes")
    if not service_token:
        raise ValueError('AMORA_VOICE_SERVICE_TOKEN is required')
    app=FastAPI(title='Amora Voice Worker',docs_url=None,redoc_url=None,openapi_url=None)

    def authenticate(authorization: str | None = Header(default=None)):
        if not authorization or not token_equal(authorization,'Bearer '+service_token):
            raise HTTPException(401,'service authentication required')

    @app.get('/live')
    def live():
        return {'status':'alive'}

    @app.get('/ready',dependencies=[Depends(authenticate)])
    def ready():
        try:
            # Load the actual model and native DSP; file presence is insufficient.
            renderer._ensure_engine()
            renderer._world._ensure_loaded()
        except Exception:
            raise HTTPException(503,'VOICE_RUNTIME_UNAVAILABLE') from None
        return {'status':'ready','voice':VOICE_ID,'model':MODEL_ID}

    import threading
    render_slot = threading.BoundedSemaphore(1)

    @app.post('/v1/render',dependencies=[Depends(authenticate)])
    def render(request: VoiceRenderRequest):
        if not render_slot.acquire(blocking=False):
            raise HTTPException(503, "VOICE_BUSY")
        try:
            audio=renderer.render(request.render_input())
            validate_wav(audio.wav_bytes)
        except Exception:
            # Worker secrets/paths/native exception strings never cross the API.
            raise HTTPException(503,'VOICE_RENDER_FAILED') from None
        finally:
            render_slot.release()
        return Response(audio.wav_bytes,media_type='audio/wav',headers={
            'Cache-Control':'no-store','X-Amora-Voice':VOICE_ID,'X-Amora-TTS-Model':MODEL_ID,
            'X-Amora-Render-Seconds':str(audio.synthesis_seconds+audio.world_seconds)})
    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail":"invalid request"},status_code=422)

    app.add_middleware(SecurityMiddleware, settings=SecuritySettings.from_environment(worker=True),
                       clock=SystemClock(), worker=True,
                       authenticate=lambda supplied: "service" if token_equal(supplied, service_token) else None)
    return app


def application():
    import os
    from amora.settings import RuntimePaths
    from .local_amora import AmoraLocalVoiceRenderer
    return create_worker(AmoraLocalVoiceRenderer(RuntimePaths.from_environment()),os.getenv('AMORA_VOICE_SERVICE_TOKEN',''))
