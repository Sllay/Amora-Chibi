"""Read-only avatar projection and opt-in playback envelope relay; no raw audio or private events."""
import asyncio
import json
import math
from time import monotonic
from uuid import uuid4
from fastapi import WebSocket, WebSocketDisconnect
from starlette.concurrency import run_in_threadpool
from amora.security import token_role

def visual_projection(state):
    return {key: getattr(state, key) for key in (
        "expression", "emotion", "intensity", "gesture", "speaking",
        "blink_allowed", "blush", "arousal", "valence"
    )}

def valid_socket(ws, core, role):
    offered = ws.headers.get("sec-websocket-protocol", "").split(",")
    credential = offered[1].strip()[5:] if len(offered) == 2 else ""
    return token_role(credential, core.secrets.get("LOCAL_API_TOKEN"), core.clock) == role

class AudioRelay:
    """Single opt-in publisher, one latest sample, no persisted data or frame queues."""
    def __init__(self):
        self.owner = None
        self.level = 0.0
        self.at = 0.0
        self.listeners = set()

    def publish(self, level):
        self.level = level
        self.at = monotonic()
        for wake in tuple(self.listeners):
            wake.set()

    def current(self):
        return self.level if monotonic() - self.at <= 0.35 else 0.0

def register_visual_stream(app, core):
    relay = AudioRelay()

    @app.websocket("/v1/avatar/visual-audio")
    async def audio(ws: WebSocket):
        if getattr(ws.state, "security_role", None) != "web" or not valid_socket(ws, core, "web"):
            await ws.close(code=4403)
            return
        if relay.owner is not None:
            await ws.close(code=4429)
            return
        owner = object()
        relay.owner = owner
        try:
            await ws.accept(subprotocol="amora.visual.audio.v1")
            last = 0.0
            burst_start = monotonic()
            count = 0
            while True:
                if not valid_socket(ws, core, "web"):
                    await ws.close(code=4401)
                    break
                try:
                    frame = await asyncio.wait_for(ws.receive(), timeout=1)
                except TimeoutError:
                    if relay.level:
                        relay.publish(0.0)
                    continue
                if frame["type"] == "websocket.disconnect":
                    break
                text = frame.get("text")
                if not isinstance(text, str) or len(text) > 128:
                    await ws.close(code=1008)
                    break
                try:
                    item = json.loads(text)
                    level = item["level"]
                    if set(item) != {"version", "level"} or item["version"] != 1 or type(level) not in {int,float} or not math.isfinite(level) or not 0 <= level <= 1:
                        raise ValueError()
                except (ValueError, TypeError, KeyError):
                    await ws.close(code=1008)
                    break
                now = monotonic()
                if now - burst_start >= 1:
                    burst_start, count = now, 0
                count += 1
                if count > 25:
                    await ws.close(code=1008)
                    break
                if now - last >= 0.08 or (level == 0 and relay.level != 0):
                    if relay.level != level or level != 0:
                        relay.publish(float(level))
                    last = now
        except WebSocketDisconnect:
            pass
        finally:
            if relay.owner is owner:
                relay.owner = None
                relay.publish(0.0)

    @app.websocket("/v1/avatar/visual-stream")
    async def stream(ws: WebSocket):
        if getattr(ws.state, "security_role", None) != "visual" or not valid_socket(ws, core, "visual"):
            await ws.close(code=4403)
            return
        await ws.accept(subprotocol="amora.visual.v1")
        session = uuid4().hex
        sequence = 0
        previous = None
        wake = asyncio.Event()
        audio_wake = asyncio.Event()
        relay.listeners.add(audio_wake)
        async def event(_event):
            wake.set()
        await core.events.subscribe("*", event)
        receiver = asyncio.create_task(ws.receive())
        waiters = []
        last_state = -10.0
        last_audio = 0.0
        try:
            async def send(kind, **payload):
                nonlocal sequence
                sequence += 1
                await asyncio.wait_for(ws.send_json({
                    "version": 1, "session": session, "sequence": sequence,
                    "timestamp": core.clock.now().isoformat(), "kind": kind, **payload
                }), timeout=5)
            while True:
                if receiver.done():
                    if receiver.result()["type"] != "websocket.disconnect":
                        await ws.close(code=1008)
                    break
                now = monotonic()
                if previous is None or now-last_state >= 4 or (wake.is_set() and now-last_state >= 1):
                    if not valid_socket(ws, core, "visual"):
                        await ws.close(code=4401)
                        break
                    wake.clear()
                    state = await run_in_threadpool(core.avatar.semantic_state)
                    projection = visual_projection(state)
                    kind = "snapshot" if previous is None else ("state" if projection != previous else "heartbeat")
                    await send(kind, **({"state": projection} if kind != "heartbeat" else {}))
                    previous = projection
                    last_state = monotonic()
                if audio_wake.is_set() or (last_audio != 0 and relay.current() == 0):
                    audio_wake.clear()
                    level = relay.current()
                    if level != 0 or last_audio != 0:
                        await send("audio", level=level)
                        last_audio = level
                # An already-set semantic wake must not busy-loop during the 1s coalescing window.
                waiters = [asyncio.create_task(audio_wake.wait())]
                if not wake.is_set():
                    waiters.append(asyncio.create_task(wake.wait()))
                timeout = min(4, max(.01, (1 if wake.is_set() else 4)-(monotonic()-last_state)))
                if last_audio:
                    timeout = min(timeout, .35)
                await asyncio.wait({receiver, *waiters}, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
                for task in waiters:
                    task.cancel()
                await asyncio.gather(*waiters, return_exceptions=True)
                waiters = []
        except (WebSocketDisconnect, TimeoutError):
            pass
        finally:
            relay.listeners.discard(audio_wake)
            import anyio
            with anyio.CancelScope(shield=True):
                for task in [receiver, *waiters]:
                    task.cancel()
                await asyncio.gather(receiver, *waiters, return_exceptions=True)
                await core.events.unsubscribe("*", event)
