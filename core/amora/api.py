from __future__ import annotations

import hmac
from dataclasses import asdict
from uuid import uuid4

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel, Field

from amora.app import AmoraCore
from amora.audio import AudioSegment, SpeakerResult, SttCredentialsError, SttError, SttRateLimitedError
from amora.events import AmoraEvent, PrivacyScope
from amora.language import PerspectiveFrame, PerspectiveStack


MAX_LOCAL_AUDIO_BYTES = 25 * 1024 * 1024


class PairingRedeemRequest(BaseModel):
    code: str = Field(pattern=r"^\d{6}$")


class SessionCreateRequest(BaseModel):
    device_id: str = Field(min_length=1, max_length=128)
    privacy_context: PrivacyScope = PrivacyScope.PERSONAL


class ObserveTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    speaker_id: str = Field(min_length=1, max_length=128)
    session_id: str | None = Field(default=None, max_length=128)
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    dedupe_key: str | None = Field(default=None, max_length=256)


class AddresseeInferRequest(BaseModel):
    text: str = Field(min_length=1, max_length=10_000)
    speaker_id: str = Field(min_length=1, max_length=128)


class ReferenceResolveRequest(BaseModel):
    expression: str = Field(min_length=1, max_length=512)
    speaker_id: str = Field(min_length=1, max_length=128)
    addressee_id: str | None = Field(default=None, max_length=128)
    focus_entity_id: str | None = Field(default=None, max_length=128)
    candidates: list[str] = Field(default_factory=list)


def create_api(core: AmoraCore) -> FastAPI:
    app = FastAPI(title="Amora Core", version="1.0")

    def expected_token() -> str:
        token = core.secrets.get("PAIRING_TOKEN")
        if not token:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="pairing token unavailable")
        return token

    def require_auth(authorization: str | None = Header(default=None)) -> None:
        prefix = "Bearer "
        if authorization is None or not authorization.startswith(prefix):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token")
        if not hmac.compare_digest(authorization[len(prefix):], expected_token()):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid bearer token")

    @app.get("/health")
    def health() -> dict[str, object]:
        return core.health_report()

    @app.post("/v1/pairing/redeem")
    def redeem_pairing(request: PairingRedeemRequest) -> dict[str, object]:
        try:
            token = core.pairing.redeem(request.code)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {"token": token}

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
            transcript = await core.stt.transcribe(
                AudioSegment(path=audio_path, mime_type="audio/wav", language=language)
            )
            rejected = (
                not transcript.text.strip()
                or transcript.provider_metadata.get("speech_rejected") == "true"
            )
            if rejected:
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
            return {
                "accepted": True,
                "transcript": transcript.model_dump(mode="json"),
                "observation": observation.model_dump(mode="json"),
                "turn": turn.model_dump(mode="json"),
                "reply_queued": reply.model_dump(mode="json") if reply else None,
            }
        except SttCredentialsError as exc:
            raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
        except SttRateLimitedError as exc:
            headers = {"Retry-After": exc.retry_after} if exc.retry_after else None
            raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc), headers=headers) from exc
        except SttError as exc:
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
