from __future__ import annotations

import json
from time import perf_counter

import httpx

from amora.latency_trace import set_ms, set_value

from .models import CognitiveContext
from .preview import preview_enabled, publish_preview
from .providers import ProviderError
from .providers_structured import (
    GroqCognitiveProvider as StructuredGroqCognitiveProvider,
    _dialogue_token_budget,
    _needs_reasoning_support,
)
from .realization import realization_instruction


class GroqCognitiveProvider(StructuredGroqCognitiveProvider):
    """Structured Groq realization with ephemeral progressive Web previews.

    The complete JSON realization is still parsed and validated by the existing
    structured runtime before it becomes canonical speech/state. When a request-
    scoped preview sink is bound, only the surface text currently present inside
    streamed ``moves[].text`` strings is published to that ephemeral sink.
    """

    async def realize(self, context: CognitiveContext) -> dict[str, object]:
        if not preview_enabled():
            return await super().realize(context)

        api_key = self.secrets.get("GROQ_API_KEY")
        if not api_key:
            raise ProviderError("GROQ_API_KEY is not configured")

        provider_total_started = perf_counter()
        set_value("provider_model", self.model)

        specialist_started = perf_counter()
        specialist_support: dict[str, object] | None = None
        specialist_error: str | None = None
        needs_specialist = _needs_reasoning_support(context)
        if needs_specialist:
            try:
                specialist_support = await self._reasoning_assist(context, api_key=api_key)
            except Exception as exc:
                specialist_error = type(exc).__name__
        specialist_elapsed_ms = (perf_counter() - specialist_started) * 1000.0 if needs_specialist else 0.0
        set_ms("specialist_ms", specialist_elapsed_ms)

        effective_context = context.model_copy(
            update={"specialist_support": specialist_support}
        ) if specialist_support else context
        messages = self._messages(effective_context)
        messages[0] = {
            **messages[0],
            "content": messages[0]["content"] + "\n\n" + realization_instruction(effective_context),
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": True,
            "temperature": 0.70,
            "top_p": 0.80,
            "max_completion_tokens": _dialogue_token_budget(effective_context),
            "reasoning_effort": "none",
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        started = perf_counter()
        timeout = httpx.Timeout(self.timeout_seconds, connect=10.0)
        content_parts: list[str] = []
        last_preview = ""
        finish_reason: str | None = None
        status_code: int | None = None
        remaining_requests: str | None = None
        remaining_tokens: str | None = None
        input_tokens: int | None = None
        output_tokens: int | None = None
        first_content_recorded = False
        first_preview_recorded = False

        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream("POST", self.API_URL, headers=headers, json=payload) as response:
                set_ms("provider_headers_ms", (perf_counter() - started) * 1000.0)
                status_code = response.status_code
                remaining_requests = response.headers.get("x-ratelimit-remaining-requests")
                remaining_tokens = response.headers.get("x-ratelimit-remaining-tokens")
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:1000]
                    raise ProviderError(
                        f"Groq structured realization failed ({response.status_code}): {body}",
                        status_code=response.status_code,
                        retry_after=response.headers.get("retry-after"),
                    )

                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        root = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = root.get("choices") or []
                    if choices:
                        choice = choices[0] or {}
                        delta = choice.get("delta") or {}
                        piece = str(delta.get("content") or "")
                        finish_reason = choice.get("finish_reason") or finish_reason
                        if piece:
                            if not first_content_recorded:
                                first_content_recorded = True
                                set_ms("provider_first_content_ms", (perf_counter() - started) * 1000.0)
                            content_parts.append(piece)
                            preview = _realization_preview_text("".join(content_parts))
                            if preview and preview != last_preview:
                                if not first_preview_recorded:
                                    first_preview_recorded = True
                                    set_ms("provider_first_preview_ms", (perf_counter() - started) * 1000.0)
                                last_preview = preview
                                await publish_preview(preview)
                    usage = root.get("usage") or {}
                    input_tokens = _optional_int(usage.get("prompt_tokens")) or input_tokens
                    output_tokens = _optional_int(usage.get("completion_tokens")) or output_tokens

        set_ms("provider_surface_ms", (perf_counter() - started) * 1000.0)
        content = "".join(content_parts).strip()
        parse_started = perf_counter()
        try:
            parsed = json.loads(content)
        except (TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ProviderError("Groq returned invalid streamed realization JSON") from exc
        finally:
            set_ms("provider_parse_ms", (perf_counter() - parse_started) * 1000.0)
        if not isinstance(parsed, dict):
            raise ProviderError("Groq realization root must be an object")

        if not last_preview:
            final_preview = _realization_preview_text(content)
            if final_preview:
                if not first_preview_recorded:
                    set_ms("provider_first_preview_ms", (perf_counter() - started) * 1000.0)
                await publish_preview(final_preview)

        set_ms("provider_total_ms", (perf_counter() - provider_total_started) * 1000.0)
        parsed["_usage"] = {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "status_code": status_code,
            "remaining_requests": remaining_requests,
            "remaining_tokens": remaining_tokens,
            "latency_ms": (perf_counter() - started) * 1000.0,
            "finish_reason": finish_reason,
            "specialist_used": specialist_support is not None,
            "specialist_error": specialist_error,
            "specialist_elapsed_ms": specialist_elapsed_ms,
            "preview_streamed": True,
        }
        return parsed


def _realization_preview_text(buffer: str) -> str:
    """Extract complete or currently-growing JSON ``text`` string values.

    This deliberately understands only JSON string escaping; it does not accept
    the frame as valid. The authoritative ``RealizationValidator`` still runs on
    the completed object before speech or canonical delivery.
    """
    values: list[str] = []
    cursor = 0
    while True:
        key = buffer.find('"text"', cursor)
        if key < 0:
            break
        colon = buffer.find(":", key + 6)
        if colon < 0:
            break
        quote = colon + 1
        while quote < len(buffer) and buffer[quote].isspace():
            quote += 1
        if quote >= len(buffer) or buffer[quote] != '"':
            cursor = colon + 1
            continue

        index = quote + 1
        chars: list[str] = []
        complete = False
        while index < len(buffer):
            char = buffer[index]
            if char == '"':
                complete = True
                index += 1
                break
            if char != "\\":
                chars.append(char)
                index += 1
                continue

            if index + 1 >= len(buffer):
                break
            escaped = buffer[index + 1]
            simple = {
                '"': '"',
                "\\": "\\",
                "/": "/",
                "b": "\b",
                "f": "\f",
                "n": "\n",
                "r": "\r",
                "t": "\t",
            }
            if escaped in simple:
                chars.append(simple[escaped])
                index += 2
                continue
            if escaped == "u":
                if index + 6 > len(buffer):
                    break
                raw = buffer[index + 2 : index + 6]
                try:
                    chars.append(chr(int(raw, 16)))
                except ValueError:
                    pass
                index += 6
                continue
            chars.append(escaped)
            index += 2

        value = "".join(chars).strip()
        if value:
            values.append(value)
        if not complete:
            break
        cursor = index

    return " ".join(values).strip()


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
