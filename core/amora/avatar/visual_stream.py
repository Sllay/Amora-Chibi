"""Read-only avatar projection, no private event contents or identifiers."""
import asyncio
from uuid import uuid4
from fastapi import WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool
from amora.security import token_role

def visual_projection(state):
    return {key: getattr(state, key) for key in (
        "expression", "emotion", "intensity", "gesture", "speaking",
        "blink_allowed", "blush", "arousal", "valence"
    )}

def register_visual_stream(app, core):
    @app.websocket("/v1/avatar/visual-stream")
    async def stream(ws: WebSocket):
        if getattr(ws.state, "security_role", None) != "visual":
            await ws.close(code=4403)
            return
        offered = ws.headers.get("sec-websocket-protocol", "").split(",")
        credential = offered[1].strip()[5:] if len(offered) == 2 else ""
        def valid():
            return token_role(credential, core.secrets.get("LOCAL_API_TOKEN"), core.clock) == "visual"
        if not valid():
            await ws.close(code=4401)
            return
        await ws.accept(subprotocol="amora.visual.v1")
        session = uuid4().hex
        sequence = 0
        previous = None
        wake = asyncio.Event()
        async def event(_event):
            wake.set()
        await core.events.subscribe("*", event)
        receiver = asyncio.create_task(ws.receive())
        waiter = None
        try:
            while True:
                if not valid():
                    await ws.close(code=4401)
                    break
                wake.clear()
                state = await run_in_threadpool(core.avatar.semantic_state)
                projection = visual_projection(state)
                sequence += 1
                kind = "snapshot" if previous is None else ("state" if projection != previous else "heartbeat")
                await asyncio.wait_for(ws.send_json({
                    "version": 1, "session": session, "sequence": sequence,
                    "timestamp": state.timestamp.isoformat(), "kind": kind,
                    **({"state": projection} if kind != "heartbeat" else {})
                }), timeout=5)
                previous = projection
                waiter = asyncio.create_task(wake.wait())
                done, _ = await asyncio.wait({receiver, waiter}, timeout=3, return_when=asyncio.FIRST_COMPLETED)
                if receiver in done:
                    if receiver.result()["type"] != "websocket.disconnect":
                        await ws.close(code=1008)
                    break
                waiter.cancel()
                await asyncio.gather(waiter, return_exceptions=True)
                # Cap readings at one per second per subscriber, even in event storms.
                done, _ = await asyncio.wait({receiver}, timeout=1)
                if receiver in done:
                    if receiver.result()["type"] != "websocket.disconnect":
                        await ws.close(code=1008)
                    break
        except (WebSocketDisconnect, TimeoutError):
            pass
        finally:
            import anyio
            with anyio.CancelScope(shield=True):
                tasks = [t for t in (receiver, waiter) if t is not None]
                for task in tasks:
                    task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)
                await core.events.unsubscribe("*", event)
