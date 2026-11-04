from __future__ import annotations

import asyncio
import json
from time import perf_counter
from typing import Any

import httpx

from .providers_nvidia import _MISTRAL_LARGE_4_MODEL, _NVIDIA_CHAT_URL


_DIAG_MARKER = "AMODEX_DIAG_TEXT_OK"


async def diagnose_nvidia_text(
    api_key: str,
    *,
    stream: bool = False,
    timeout_seconds: float = 55.0,
    model: str = _MISTRAL_LARGE_4_MODEL,
) -> dict[str, Any]:
    """Read-only minimal NVIDIA transport probe; never touches Core or promotion.

    Return only safe timing, HTTP status, and exact marker match; never return
    provider text, credentials, response bodies or reasoning traces.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "user", "content": "Responda exatamente AMODEX_DIAG_TEXT_OK"}],
        "stream": stream,
        "reasoning_effort": "high",
        "max_tokens": 4096,
        "temperature": 0.5,
        "top_p": 1,
        "frequency_penalty": 0,
        "presence_penalty": 0,
    }
    started = perf_counter()
    status_code: int | None = None
    first_content_ms: float | None = None
    pieces: list[str] = []
    completed = False
    try:
        async with asyncio.timeout(timeout_seconds):
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(timeout_seconds, connect=min(10.0, timeout_seconds)),
                follow_redirects=False,
            ) as client:
                async with client.stream(
                    "POST",
                    _NVIDIA_CHAT_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "Accept": "text/event-stream" if stream else "application/json",
                    },
                    json=payload,
                ) as response:
                    status_code = response.status_code
                    if response.status_code >= 400:
                        return _result(started, stream, status_code, first_content_ms, False, "HTTP_ERROR")
                    if stream:
                        if "text/event-stream" not in response.headers.get("content-type", ""):
                            return _result(started, stream, status_code, first_content_ms, False, "UNEXPECTED_CONTENT_TYPE")
                        async for line in response.aiter_lines():
                            if not line.startswith("data:"):
                                continue
                            value = line[5:].strip()
                            if value == "[DONE]":
                                completed = True
                                break
                            if not value:
                                continue
                            event = json.loads(value)
                            if not isinstance(event, dict) or event.get("error"):
                                return _result(started, stream, status_code, first_content_ms, False, "SSE_ERROR")
                            for choice in event.get("choices", []):
                                delta = choice.get("delta") or {}
                                chunk = delta.get("content") if isinstance(delta, dict) else None
                                if isinstance(chunk, str) and chunk:
                                    if first_content_ms is None:
                                        first_content_ms = (perf_counter() - started) * 1000
                                    pieces.append(chunk)
                                    if sum(map(len, pieces)) > 16384:
                                        return _result(started, stream, status_code, first_content_ms, False, "CONTENT_LIMIT")
                    else:
                        # httpx.AsyncClient.stream() exposes an unread response.
                        # Response.json() raises ResponseNotRead until we consume it.
                        # Bound body size to keep this diagnostic predictable.
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 131_072:
                                return _result(started, stream, status_code, first_content_ms, False, "CONTENT_LIMIT")
                        data = json.loads(body)
                        content = data["choices"][0]["message"]["content"]
                        if not isinstance(content, str):
                            return _result(started, stream, status_code, first_content_ms, False, "INVALID_RESPONSE")
                        pieces.append(content)
                        completed = True
        return _result(
            started, stream, status_code, first_content_ms,
            completed and "".join(pieces).strip() == _DIAG_MARKER,
            "OK" if completed else "STREAM_INCOMPLETE",
        )
    except (TimeoutError, httpx.TimeoutException):
        return _result(started, stream, status_code, first_content_ms, False, "TIMEOUT")
    except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError):
        return _result(started, stream, status_code, first_content_ms, False, "TRANSPORT_OR_FORMAT_ERROR")


def _result(
    started: float, stream: bool, status_code: int | None,
    first_content_ms: float | None, exact_match: bool, outcome: str,
) -> dict[str, Any]:
    return {
        "stream": stream,
        "status_code": status_code,
        "elapsed_ms": round((perf_counter() - started) * 1000, 1),
        "first_content_ms": round(first_content_ms, 1) if first_content_ms is not None else None,
        "exact_match": exact_match,
        "outcome": outcome if not (outcome == "OK" and not exact_match) else "MARKER_MISMATCH",
    }
