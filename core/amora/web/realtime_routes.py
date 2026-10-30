from __future__ import annotations

import re
from collections import OrderedDict
from time import monotonic
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from amora.cognition.preview import bind_preview_sink
from amora.voice.backend import VoiceBackend

from .routes import register_web_routes as register_base_web_routes


_PREVIEW_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,96}$")
_MAX_PREVIEWS = 128
_PREVIEW_TTL_SECONDS = 180.0


def register_web_routes(
    app: FastAPI,
    core,
    require_auth: Callable[..., None],
    *,
    voice: VoiceBackend | None = None,
) -> None:
    """Register base Web routes plus ephemeral progressive cognition previews.

    Preview reads intentionally reuse the already-authorized ``/v1/web/state``
    surface instead of widening the browser credential scope with a new route.
    ``SecurityMiddleware`` remains the outer access-control boundary.
    """
    register_base_web_routes(app, core, require_auth, voice=voice)
    previews: OrderedDict[str, dict[str, object]] = OrderedDict()

    def prune() -> None:
        cutoff = monotonic() - _PREVIEW_TTL_SECONDS
        for key in list(previews):
            if float(previews[key].get("updated", 0.0)) >= cutoff:
                continue
            previews.pop(key, None)
        while len(previews) > _MAX_PREVIEWS:
            previews.popitem(last=False)

    def put(preview_id: str, **values: object) -> None:
        item = previews.get(preview_id, {"text": "", "done": False, "error": None})
        item.update(values)
        item["updated"] = monotonic()
        previews[preview_id] = item
        previews.move_to_end(preview_id)
        prune()

    def read(preview_id: str) -> dict[str, object]:
        if not _PREVIEW_ID_RE.fullmatch(preview_id):
            return {"text": "", "done": True, "error": "INVALID_PREVIEW_ID"}
        prune()
        item = previews.get(preview_id)
        if item is None:
            return {"text": "", "done": False, "error": None}
        return {
            "text": str(item.get("text") or ""),
            "done": bool(item.get("done", False)),
            "error": item.get("error"),
        }

    @app.middleware("http")
    async def bind_web_preview(request, call_next):
        path = request.url.path

        # Browser credentials already have GET access to /v1/web/state. A query
        # parameter selects the ephemeral preview view without granting another
        # endpoint or exposing the main Core state payload to this helper.
        if path == "/v1/web/state" and request.method == "GET" and "preview_id" in request.query_params:
            return JSONResponse(read(request.query_params.get("preview_id", "")))

        preview_id = request.headers.get("x-amora-preview-id", "").strip()
        eligible = path == "/v1/web/utterance" and bool(_PREVIEW_ID_RE.fullmatch(preview_id))
        if not eligible:
            return await call_next(request)

        put(preview_id, text="", done=False, error=None)

        async def publish(text: str) -> None:
            put(preview_id, text=text, done=False, error=None)

        try:
            with bind_preview_sink(publish):
                response = await call_next(request)
        except Exception as exc:
            put(preview_id, done=True, error=type(exc).__name__)
            raise
        put(preview_id, done=True, error=None)
        return response
