"""Standalone ASGI voice worker. Does not bootstrap or import AmoraCore."""
import hmac
from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import Response
from .contract import VoiceRenderRequest
from .local_amora import LocalVoiceUnavailable, MODEL_ID, VOICE_ID
from .remote import validate_wav


def create_worker(renderer, service_token):
    if not service_token:
        raise ValueError('AMORA_VOICE_SERVICE_TOKEN is required')
    app=FastAPI(title='Amora Voice Worker',docs_url=None,redoc_url=None,openapi_url=None)

    def authenticate(authorization: str | None = Header(default=None)):
        if not authorization or not hmac.compare_digest(authorization,'Bearer '+service_token):
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

    @app.post('/v1/render',dependencies=[Depends(authenticate)])
    def render(request: VoiceRenderRequest):
        try:
            audio=renderer.render(request.render_input())
            validate_wav(audio.wav_bytes)
        except Exception:
            # Worker secrets/paths/native exception strings never cross the API.
            raise HTTPException(503,'VOICE_RENDER_FAILED') from None
        return Response(audio.wav_bytes,media_type='audio/wav',headers={
            'Cache-Control':'no-store','X-Amora-Voice':VOICE_ID,'X-Amora-TTS-Model':MODEL_ID,
            'X-Amora-Render-Seconds':str(audio.synthesis_seconds+audio.world_seconds)})
    return app


def application():
    import os
    from amora.settings import RuntimePaths
    from .local_amora import AmoraLocalVoiceRenderer
    return create_worker(AmoraLocalVoiceRenderer(RuntimePaths.from_environment()),os.getenv('AMORA_VOICE_SERVICE_TOKEN',''))
