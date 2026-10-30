from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
import logging
import sys
from dataclasses import asdict
from uuid import uuid4
from time import perf_counter

from fastapi.responses import JSONResponse
from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field
from typing import Literal
from fastapi.exceptions import RequestValidationError
from amora.security import SecurityMiddleware, SecuritySettings, token_role, issue_browser_token, token_equal

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
    code: str = Field(pattern=r"^[0-9]{6}$")
    client: Literal["web", "avatar"] = "web"


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


def create_api(core: AmoraCore, *, own_core: bool = False, voice=None) -> FastAPI:
    # High-frequency request polling and successful provider requests remain in
    # structured diagnostics, not in the human terminal surface.
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(app):
        app.state.accepting = True
        try:
            yield
        finally:
            app.state.accepting = False
            console = getattr(app.state, "server_console", None)
            try:
                if console is not None:
                    console.close()
            finally:
                try:
                    backend = getattr(app.state, "web_local_voice", None)
                    if hasattr(backend, "close"):
                        backend.close()
                finally:
                    if own_core:
                        core.close()

    settings = SecuritySettings.from_environment()
    if settings.cloud and len((core.secrets.get("LOCAL_API_TOKEN") or "").encode()) < 32:
        raise ValueError("cloud admin token must contain at least 32 bytes")
    app = FastAPI(title="Amora Core API", version=__version__, lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.accepting = True
    app.state.server_console = None
    app.state.voice_backend = voice

    def expected_token() -> str:
        value = core.secrets.get("LOCAL_API_TOKEN")
        if not value:
            raise RuntimeError("LOCAL_API_TOKEN is missing")
        return value

    def require_auth(request: Request, authorization: str | None = Header(default=None)) -> None:
        if getattr(request.state, "security_role", None) in {"admin", "web", "avatar"}:
            return
        prefix = "Bearer "
        if authorization is None or not authorization.startswith(prefix):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="authentication required")
        supplied = authorization[len(prefix):]
        if not token_equal(supplied, expected_token()):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials")

    def server_console(request: Request | None = None) -> ServerConsole | None:
        existing = getattr(app.state, "server_console", None)
        if existing is not None:
            return existing
        if settings.cloud or not bool(getattr(sys.stderr, "isatty", lambda: False)()):
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
        correlation_id = str(uuid4())
        request.state.correlation_id = correlation_id
        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception as exc:
            core.operations.error("api", "REQUEST_FAILED", correlation_id=correlation_id)
            from amora.observability import request_failure_fields
            route_path = getattr(request.scope.get("route"), "path", "/unmatched")
            logging.getLogger("amora.api").error("request_failed", extra={
                "method": request.method if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"} else "OTHER",
                "path": route_path,
                "stage": getattr(request.state, "pipeline_stage", "request"),
                **request_failure_fields(exc),
                "error_code": "REQUEST_FAILED", "exception_type": type(exc).__name__,
                "correlation_id": correlation_id, "latency_ms": (perf_counter()-started)*1000})
            if console is not None:
                console.record_error("api", f"{request.method} {route_path}: {type(exc).__name__}")
            raise
        response.headers["X-Correlation-ID"] = correlation_id
        if response.status_code >= 500:
            core.operations.error("api", "HTTP_"+str(response.status_code), correlation_id=correlation_id)
        if console is not None:
            if response.status_code >= 500 and not request.url.path.startswith("/v1/voice/"):
                console.record_error("api", f"{request.method} {request.url.path} → HTTP {response.status_code}")
            console.heartbeat(core)
        return response

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
        try:
            integrity = core.database.integrity_check() if not core._closed else "closed"
        except Exception:
            integrity = "unavailable"
        return JSONResponse({"status": "ok" if integrity == "ok" else "degraded",
                             **({} if settings.cloud else {"version": __version__, "database_integrity": integrity})},
                            status_code=200 if integrity == "ok" else 503)

    @app.get("/live")
    def live():
        return {"status": "alive"}

    @app.get("/ready")
    def ready():
        report = core.readiness() if app.state.accepting else {"status": "not_ready", "error_code": "DRAINING"}
        if settings.cloud:
            report = {"status": report["status"]}
        return JSONResponse(report, status_code=200 if report["status"] == "ready" else 503,
                            headers={"Cache-Control": "no-store"})

    @app.get("/v1/operations/state", dependencies=[Depends(require_auth)])
    def operations_state():
        from amora.operations import operational_snapshot
        return operational_snapshot(core, getattr(app.state, "web_local_voice", None))

    @app.post("/v1/pairing/redeem")
    def redeem_pairing(request: PairingRedeemRequest) -> dict[str, str]:
        if not core.pairing.redeem(request.code):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid or expired pairing code")
        token = issue_browser_token(expected_token(), core.clock, request.client) if settings.cloud or request.client == "web" else expected_token()
        return {"token": token, "token_type": "Bearer"}

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
        test_mode: bool = Query(default=False),
        speech_device_id: str | None = Query(default=None, min_length=1, max_length=128),
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
            request.state.pipeline_stage = "stt.transcribe"
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
                    "test_mode": test_mode,
                }

            resolved_speech_device = speech_device_id or (
                "WEB_SAFARI" if speaker_id == "PERSON_BK" else "DEVICE_A15"
            )
            speaker = SpeakerResult(
                speaker_id=speaker_id,
                confidence=speaker_confidence,
                reason_codes=[
                    "WEB_CLIENT_DECLARED_SPEAKER"
                    if resolved_speech_device == "WEB_SAFARI"
                    else "ANDROID_CLIENT_DECLARED_SPEAKER"
                ],
            )
            request.state.pipeline_stage = "language_social"
            observation = await core.language_social.observe_transcript(
                transcript,
                speaker,
                session_id=session_id,
                privacy_scope=privacy_scope,
                dedupe_key=f"stt:{transcript.request_id}" if transcript.request_id else None,
                explicit_addressee_id="ENTITY_AMORA" if speech_device_id == "WEB_SAFARI" else None,
            )
            if console is not None:
                console.set_activity("PENSANDO", "Core local → plano semântico → linguagem", ttl_seconds=60.0)
            metadata: dict[str, object] = {"speech_device_id": resolved_speech_device}
            if speech_device_id == "WEB_SAFARI":
                metadata["explicit_surface_target"] = "ENTITY_AMORA"
            if test_mode:
                metadata.update(
                    {
                        "test_mode": True,
                        "memory_consolidation": False,
                        "relationship_learning": False,
                        "emotion_persistence": False,
                    }
                )
            try:
                request.state.pipeline_stage = "conversation"
                turn, reply = await core.conversation.observe_completed_utterance(
                    transcript.text,
                    speaker_id=speaker.speaker_id,
                    addressee_id=observation.addressee.entity_id,
                    source_event_id=observation.event_id,
                    privacy_scope=privacy_scope,
                    remote_processing_allowed=remote_processing_allowed,
                    session_id=session_id,
                    stt_confidence=transcript.confidence,
                    metadata=metadata,
                )
            finally:
                if speech_device_id != "WEB_SAFARI":
                    core.social.end_turn(speaker.speaker_id)
            if console is not None:
                if reply is not None:
                    console.set_activity("RESPONDENDO", f"fala enfileirada → {resolved_speech_device}", ttl_seconds=4.0)
                else:
                    console.set_activity("IDLE", "sem resposta vocal", ttl_seconds=1.5)
            return {
                "accepted": True,
                "transcript": transcript.model_dump(mode="json"),
                "observation": observation.model_dump(mode="json"),
                "turn": turn.model_dump(mode="json"),
                "reply_queued": reply.model_dump(mode="json") if reply else None,
                "test_mode": test_mode,
                "speech_device_id": resolved_speech_device,
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
            if event.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
                return
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(event)

        await core.events.subscribe("*", receive_event)
        receive_task = asyncio.create_task(websocket.receive())
        queued_task = None
        try:
            while True:
                queued_task = asyncio.create_task(queue.get())
                done, _ = await asyncio.wait({receive_task, queued_task}, return_when=asyncio.FIRST_COMPLETED)
                if receive_task in done:
                    message = receive_task.result()
                    if message["type"] == "websocket.disconnect":
                        break
                    await websocket.close(code=1008)
                    break  # server-to-client stream has no inbound command protocol
                if queued_task in done:
                    await websocket.send_json(queued_task.result().model_dump(mode="json"))
                else:
                    queued_task.cancel()
                    await asyncio.gather(queued_task, return_exceptions=True)
        except WebSocketDisconnect:
            pass
        finally:
            tasks = [task for task in (receive_task, queued_task) if task is not None]
            for task in tasks:
                task.cancel()
            import anyio
            with anyio.CancelScope(shield=True):
                await asyncio.gather(*tasks, return_exceptions=True)
                await core.events.unsubscribe("*", receive_event)

    def browser_access(role, scope):
        from urllib.parse import parse_qs
        path = scope["path"]
        method = scope["method"]
        import re
        if role == "avatar":
            fixed = {"/v1/avatar/state": {"GET"}, "/v1/avatar/project": {"GET", "PUT"},
                     "/v1/avatar/assets": {"GET"}}
            if method in fixed.get(path, set()):
                return True
            return bool(re.fullmatch(r"/v1/avatar/assets/[A-Za-z0-9_.-]{1,96}", path)) and method in {"GET", "PUT", "DELETE"}
        if path == "/v1/web/state":
            return method == "GET"
        if path == "/v1/web/utterance":
            return method == "POST"
        query = parse_qs(scope.get("query_string", b"").decode("utf-8", "replace"))
        if path == "/v1/audio/transcribe":
            return method == "POST" and query.get("speech_device_id") == ["WEB_SAFARI"]
        if path == "/v1/conversation/next":
            return method == "GET" and query.get("device_id") == ["WEB_SAFARI"]
        match = re.fullmatch(r"/v1/(web/voice|conversation)/utterances/([0-9a-fA-F-]{36})/(audio|stream|started|ack|cancel)", path)
        if match:
            prefix, utterance_id, action = match.groups()
            if not ((prefix == "web/voice" and action in {"audio", "stream"} and method == "GET") or
                    (prefix == "conversation" and action in {"started", "ack", "cancel"} and method == "POST")):
                return False
            try:
                item = core.conversation.store.get_utterance(utterance_id)
                return item is not None and item.device_id == "WEB_SAFARI"
            except ValueError:
                return False
        return False

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse({"detail": "invalid request"}, status_code=422)

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        return JSONResponse({"detail": "internal error"}, status_code=500)

    app.add_middleware(SecurityMiddleware, settings=settings, clock=core.clock,
                       authenticate=lambda supplied: token_role(supplied, expected_token(), core.clock),
                       web_access=browser_access)
    return app