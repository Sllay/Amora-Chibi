from __future__ import annotations

import re
from collections import OrderedDict
from time import monotonic
from typing import Callable

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from amora.cognition.preview import bind_preview_sink
from amora.latency_trace import bind_latency_trace, install_remote_storage_probe
from amora.voice.backend import VoiceBackend

from .routes import register_web_routes as register_base_web_routes


_PREVIEW_ID_RE = re.compile(r"^[A-Za-z0-9._-]{8,96}$")
_MAX_PREVIEWS = 128
_PREVIEW_TTL_SECONDS = 180.0
_TRACE_HEADERS = {
    "context_compile_ms": "X-Amora-Latency-Context-Ms",
    "provider_total_ms": "X-Amora-Latency-Provider-Total-Ms",
    "provider_surface_ms": "X-Amora-Latency-Provider-Surface-Ms",
    "provider_headers_ms": "X-Amora-Latency-Provider-Headers-Ms",
    "provider_first_content_ms": "X-Amora-Latency-Provider-First-Content-Ms",
    "provider_first_preview_ms": "X-Amora-Latency-Provider-First-Preview-Ms",
    "provider_parse_ms": "X-Amora-Latency-Provider-Parse-Ms",
    "specialist_ms": "X-Amora-Latency-Specialist-Ms",
    "first_preview_from_request_ms": "X-Amora-Latency-First-Preview-Ms",
    "remote_sql_ms": "X-Amora-Latency-Remote-Sql-Ms",
    "remote_sql_calls": "X-Amora-Latency-Remote-Sql-Calls",
    "remote_sql_max_ms": "X-Amora-Latency-Remote-Sql-Max-Ms",
    "remote_sql_read_ms": "X-Amora-Latency-Remote-Sql-Read-Ms",
    "remote_sql_read_calls": "X-Amora-Latency-Remote-Sql-Read-Calls",
    "remote_sql_write_ms": "X-Amora-Latency-Remote-Sql-Write-Ms",
    "remote_sql_write_calls": "X-Amora-Latency-Remote-Sql-Write-Calls",
    "remote_sql_txn_ms": "X-Amora-Latency-Remote-Sql-Txn-Ms",
    "remote_sql_txn_calls": "X-Amora-Latency-Remote-Sql-Txn-Calls",
    "remote_sql_other_ms": "X-Amora-Latency-Remote-Sql-Other-Ms",
    "remote_sql_other_calls": "X-Amora-Latency-Remote-Sql-Other-Calls",
}


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
    install_remote_storage_probe()
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

        # Safari Lab assets change frequently during physical certification.
        # Do not let a long-lived module cache keep an older runtime after deploy.
        if path == "/web" or path.startswith("/web/"):
            response = await call_next(request)
            response.headers["Cache-Control"] = "no-store, max-age=0"
            return response

        # Browser credentials already have GET access to /v1/web/state. A query
        # parameter selects the ephemeral preview view without granting another
        # endpoint or exposing the main Core state payload to this helper.
        if path == "/v1/web/state" and request.method == "GET" and "preview_id" in request.query_params:
            return JSONResponse(read(request.query_params.get("preview_id", "")), headers={"Cache-Control": "no-store"})

        preview_id = request.headers.get("x-amora-preview-id", "").strip()
        eligible = path == "/v1/web/utterance" and bool(_PREVIEW_ID_RE.fullmatch(preview_id))
        if not eligible:
            return await call_next(request)

        put(preview_id, text="", done=False, error=None)
        request_started = monotonic()

        with bind_latency_trace() as trace:
            async def publish(text: str) -> None:
                if "first_preview_from_request_ms" not in trace:
                    trace["first_preview_from_request_ms"] = round(
                        (monotonic() - request_started) * 1000.0,
                        3,
                    )
                put(preview_id, text=text, done=False, error=None)

            try:
                with bind_preview_sink(publish):
                    response = await call_next(request)
            except Exception as exc:
                put(preview_id, done=True, error=type(exc).__name__)
                raise

        put(preview_id, done=True, error=None)
        response.headers["X-Amora-Latency-Trace-Version"] = "v3"
        for key, header in _TRACE_HEADERS.items():
            value = trace.get(key)
            if value is not None:
                response.headers[header] = str(value)
        return response
