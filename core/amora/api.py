from __future__ import annotations

import hmac
from dataclasses import asdict

from fastapi import Depends, FastAPI, Header, HTTPException, WebSocket, WebSocketDisconnect, status
from pydantic import BaseModel

from amora.app import AmoraCore
from amora.events import AmoraEvent, PrivacyScope
from amora.identity import PrivacyContext


class SessionCreateRequest(BaseModel):
    device_id: str = "DEVICE_A15"
    privacy_context: PrivacyContext = PrivacyContext.PRIVATE


def create_api(core: AmoraCore) -> FastAPI:
    app = FastAPI(title="Amora Core API", version="0.1.0.dev1")

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

    @app.get("/health")
    def health() -> dict[str, object]:
        report = core.health_report()
        return {"status": report["status"], "version": report["version"], "database_integrity": report["database_integrity"]}

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
            session = core.sessions.start_local_session(request.device_id, privacy_context=request.privacy_context)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return session.model_dump(mode="json")

    @app.delete("/v1/sessions/{session_id}", dependencies=[Depends(require_auth)])
    def close_session(session_id: str) -> dict[str, bool]:
        return {"closed": core.sessions.close(session_id)}

    @app.websocket("/v1/events/stream")
    async def event_stream(websocket: WebSocket) -> None:
        authorization = websocket.headers.get("authorization")
        prefix = "Bearer "
        if authorization is None or not authorization.startswith(prefix) or not hmac.compare_digest(authorization[len(prefix):], expected_token()):
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
