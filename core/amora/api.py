from __future__ import annotations

import hmac
import logging
import sys
from dataclasses import asdict
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from amora import __version__
from amora.actions.routes import register_action_routes
from amora.app import AmoraCore
from amora.audio import AudioSegment, SpeakerResult, SttCredentialsError, SttError, SttRateLimitedError
from amora.autonomy.routes import register_autonomy_routes
from amora.avatar.routes import register_avatar_routes
from amora.cognition.routes import register_cognition_routes
from amora.conversation.routes import register_conversation_routes
from amora.emotion.routes import register_emotion_routes
from amora.events import AmoraEvent, PrivacyScope
from amora.identity import PrivacyContext
from amora.language import PerspectiveFrame, PerspectiveStack
from amora.memory.routes import register_memory_routes
from amora.server_console import ServerConsole
from amora.world.routes import register_world_routes


MAX_LOCAL_AUDIO_BYTES = 12 * 1024 * 1024


class SessionCreateRequest(BaseModel):
    device_id: str = "DEVICE_A15"
    privacy_context: PrivacyContext = PrivacyContext.PRIVATE


class PairingRedeemRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class AddresseeInferRequest(BaseModel):
    text: str
    speaker_id: str = "PERSON_BK"


class ReferenceResolveRequest(BaseModel):
    expression: str
    speaker_id: str = "PERSON_BK"
    addressee_id: str | None = "ENTITY_AMORA"
    focus_entity_id: str | None = None
    candidates: list[str] = Field(default_factory=list)


class ObserveTextRequest(BaseModel):
    text: str
    speaker_id: str = "PERSON_BK"
    session_id: str | None = None
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    dedupe_key: str | None = None


def create_api(core: AmoraCore) -> FastAPI:
    # High-frequency request polling and successful provider requests remain in
    # structured diagnostics, not in the human terminal surface.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    app = FastAPI(title="Amora Core API", version=__version__)
    app.state.server_console = None

    def expected_token() -> str:
        value = core.secrets.get("LOCAL_API_TOKEN")
        if not value:
            raise RuntimeError("LOCAL_API_TOKEN is missing")
        return value

    def require_auth(authorization: str | None = Header(default=None)) -> None:
        prefix = "Bearer "
        if authorization is None or not authorization.startswith(prefix):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
        supplied = authorization[len(prefix):]
        if not hmac.compare_digest(supplied, expected_token()):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    def server_console(request: Request | None = None) -> ServerConsole | None:
        existing = getattr(app.state, "server_console", None)
        if existing is not None:
            return existing
        if not bool(getattr(sys.stderr, "isatty", lambda: False)()):
            return None
        host = request.url.hostname if request is not None else "127.0.0.1"
        port = request.url.port if request is not None and request.url.port is not None else 8765
        console = ServerConsole(
            host=host or "127.0.0.1",
            port=port,
            log_path=str(core.paths.logs / "amora.jsonl"),
        )
        app.state.server_console = console
        console.start(core)
        return console

    @app.middleware("http")
    async def server_console_middleware(request: Request, call_next):
        console = server_console(request)
        try:
            response = await call_next(request)
        except Exception as exc:
            if console is not None:
                console.record_error("api", f"{request.method} {request.url.path}: {type(exc).__name__}: {exc}")
            raise
        if console is not None:
            if response.status_code >= 500 and not request.url.path.startswith("/v1/voice/"):
                console.record_error("api", f"{request.method} {request.url.path} → HTTP {response.status_code}")
            console.heartbeat(core)
        return response

    # FastAPI 0.143 removed the legacy app.add_event_handler surface used by
    # older releases. The dashboard only owns terminal/logging presentation and
    # does not hold canonical state, so process teardown is sufficient. On older
    # FastAPI versions, close it cleanly when the compatibility hook exists.
    add_event_handler = getattr(app, "add_event_handler", None)
    if callable(add_event_handler):
        def close_server_console() -> None:
            console = getattr(app.state, "server_console", None)
            if console is not None:
                console.close()
        add_event_handler("shutdown", close_server_console)

    register_memory_routes(app, core, require_auth)
    register_world_routes(app, core, require_auth)
    register_emotion_routes(app, core, require_auth)
    register_autonomy_routes(app, core, require_auth)
    register_action_routes(app, core, require_auth)
    register_conversation_routes(app, core, require_auth)
    register_cognition_routes(app, core, require_auth)
    register_avatar_routes(app, core, require_auth)

    @app.get("/health")
    def health() -> dict[str, object]:
        report = core.health_report()
        return {
            "status": report["status"],
            "version": report["version"],
            "database_integrity": report["database_integrity"],
        }

    @app.post("/v1/pairing/redeem")
    def redeem_pairing(request: PairingRedeemRequest) -> dict[str, str]:
        if not core.pairing.redeem(request.code):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired pairing code")
        return {"token": expected_token(), "token_type": "Bearer"}

    @app.get("/v1/state", dependencies=[Depends(require_auth)])
    def state_view() -> dict[str, object]:
        return core.health_report()

    @app.post("/v1/events", dependencies=[Depends(require_auth)])
    async def publish_event(event: AmoraEvent) -> dict[str, object]:
        result = await core.events.publish(event)
        return asdict(result)

    @app.post("/v1/sessions", dependencies=[Depends(require_auth)])
    def create_session(request: SessionCreateRequest) -> dict[str, object]:
        try:
            session = core.sessions.start_local_session(
                request.device_id,
                privacy_context=request.privacy_context,
            )
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return session.model_dump(mode="json")

    @app.delete("/v1/sessions/{session_id}", dependencies=[Depends(require_auth)])
    def close_session(session_id: str) -> dict[str, bool]:
        return {"closed": core.sessions.close(session_id)}

    @app.post("/v1/language/addressee", dependencies=[Depends(require_auth)])
    def infer_addressee(request: AddresseeInferRequest) -> dict[str, object]:
        result = core.addressee.infer(
            request.text,
            speaker_id=request.speaker_id,
            scene=core.social.scene,
        )
        return result.model_dump(mode="json")

    @app.post("/v1/language/resolve", dependencies=[Depends(require_auth)])
    def resolve_reference(request: ReferenceResolveRequest) -> dict[str, object]:
        stack = PerspectiveStack(
            PerspectiveFrame(
                speaker_id=request.speaker_id,
                addressee_id=request.addressee_id,
                focus_entity_id=request.focus_entity_id,
            )
        )
        result = core.perspective.resolve(
            request.expression,
            stack,
            candidates=request.candidates,
        )
        return result.model_dump(mode="json")

    @app.post("/v1/language/observe", dependencies=[Depends(require_auth)])
    async def observe_text(request: ObserveTextRequest) -> dict[str, object]:
        result = await core.language_social.observe_text(
            request.text,
            speaker_id=request.speaker_id,
            session_id=request.session_id,
            privacy_scope=request.privacy_scope,
            dedupe_key=request.dedupe_key,
            source="api:text",
        )
        return result.model_dump(mode="json")

    @app.post("/v1/audio/transcribe", dependencies=[Depends(require_auth)])
    async def transcribe_audio(
        request: Request,
        language: str | None = Query(default="pt", min_length=2, max_length=16),
        speaker_id: str = Query(default="SESSION_ANDROID_SPEAKER_1", min_length=1, max_length=128),
        speaker_confidence: float = Query(default=0.35, ge=0.0, le=1.0),
        session_id: str | None = Query(default=None, max_length=128),
        privacy_scope: PrivacyScope = Query(default=PrivacyScope.PERSONAL),
        remote_processing_allowed: bool = Query(default=False),
    ) -> dict[str, object]:
        console = server_console(request)
        content_type = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        if content_type not in {"audio/wav", "audio/x-wav", "audio/wave"}:
            raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="expected audio/wav")

        body = await request.body()
        if not body:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="empty audio body")
        if len(body) > MAX_LOCAL_AUDIO_BYTES:
            raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail="audio body too large")

        incoming = core.paths.cache / "incoming"
        incoming.mkdir(parents=True, exist_ok=True)
        audio_path = incoming / f"{uuid4().hex}.wav"
        audio_path.write_bytes(body)

        try:
            if console is not None:
                console.set_activity("TRANSCRIBINDO", "Groq STT", ttl_seconds=45.0)
            transcript = await core.stt.transcribe(
                AudioSegment(path=audio_path, mime_type="audio/wav", language=language)
            )
            if console is not None:
                console.clear_module_override("groq.stt")
            rejected = (
                not transcript.text.strip()
                or transcript.provider_metadata.get("speech_rejected") == "true"
            )
            if rejected:
                if console is not None:
                    console.set_activity("IDLE", "fala rejeitada pelo VAD/STT", ttl_seconds=1.5)
                return {
                    "accepted": False,
                    "transcript": transcript.model_dump(mode="json"),
                    "observation": None,
                    "turn": None,
                    "reply_queued": None,
                }

            speaker = SpeakerResult(
                speaker_id=speaker_id,
                confidence=speaker_confidence,
                reason_codes=["ANDROID_CLIENT_DECLARED_SPEAKER"],
            )
            observation = await core.language_social.observe_transcript(
                transcript,
                speaker,
                session_id=session_id,
                privacy_scope=privacy_scope,
                dedupe_key=f"stt:{transcript.request_id}" if transcript.request_id else None,
            )
            if console is not None:
                console.set_activity("PENSANDO", "Core local → plano semântico → linguagem", ttl_seconds=60.0)
            try:
                turn, reply = await core.conversation.observe_completed_utterance(
                    transcript.text,
                    speaker_id=speaker.speaker_id,
                    addressee_id=observation.addressee.entity_id,
                    source_event_id=observation.event_id,
                    privacy_scope=privacy_scope,
                    remote_processing_allowed=remote_processing_allowed,
                    session_id=session_id,
                    stt_confidence=transcript.confidence,
                )
            finally:
                core.social.end_turn(speaker.speaker_id)
            if console is not None:
                if reply is not None:
                    console.set_activity("RESPONDENDO", "fala enfileirada", ttl_seconds=4.0)
                else:
                    console.set_activity("IDLE", "sem resposta vocal", ttl_seconds=1.5)
            return {
                "accepted": True,
                "transcript": transcript.model_dump(mode="json"),
                "observation": observation.model_dump(mode="json"),
                "turn": turn.model_dump(mode="json"),
                "reply_queued": reply.model_dump(mode="json") if reply else None,
            }
        except SttCredentialsError as exc:
            if console is not None:
                console.set_module("groq.stt", "Groq STT", "OFF", "credencial indisponível")
                console.record_error("Groq STT", str(exc))
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except SttRateLimitedError as exc:
            if console is not None:
                console.set_module("groq.stt", "Groq STT", "WAIT", "rate limit")
            headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc), headers=headers) from exc
        except SttError as exc:
            if console is not None:
                console.set_module("groq.stt", "Groq STT", "DEGRADED", type(exc).__name__)
                console.record_error("Groq STT", str(exc))
            raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc
        finally:
            try:
                audio_path.unlink()
            except FileNotFoundError:
                pass

    @app.websocket("/v1/events/stream")
    async def event_stream(websocket: WebSocket) -> None:
        authorization = websocket.headers.get("authorization")
        prefix = "Bearer "
        if (
            authorization is None
            or not authorization.startswith(prefix)
            or not hmac.compare_digest(authorization[len(prefix):], expected_token())
        ):
            await websocket.close(code=4401)
            return
        await websocket.accept()
        import asyncio

        queue: asyncio.Queue[AmoraEvent] = asyncio.Queue(maxsize=128)

        async def receive_event(event: AmoraEvent) -> None:
            if event.privacy_scope == PrivacyScope.AUTH:
                return
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

        await core.events.subscribe("*", receive_event)
        try:
            while True:
                event = await queue.get()
                await websocket.send_json(event.model_dump(mode="json"))
        except WebSocketDisconnect:
            pass
        finally:
            await core.events.unsubscribe("*", receive_event)

    return app
