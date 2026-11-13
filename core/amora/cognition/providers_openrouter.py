from __future__ import annotations

import json
from time import perf_counter
from typing import Any

import httpx

from .models import CognitiveContext
from .prompt_frame import render_trusted_protocol
from .providers import ProviderError
from .providers_identity import (
    OpenRouterCognitiveProvider as BaseOpenRouterCognitiveProvider,
    _IDENTITY_CONTRACT,
    _responses_input,
)
from .providers_v1 import _REFLECTION_INSTRUCTION
from .reflection_contract import is_memory_only, memory_input, memory_instructions, validate_memory_extraction
from .realization import realization_instruction


_OPENROUTER_RESPONSES_URL = "https://openrouter.ai/api/v1/responses"


class OpenRouterCognitiveProvider(BaseOpenRouterCognitiveProvider):
    """OpenRouter last-resort provider using native Responses instructions/input.

    The free route can resolve to different underlying models, so production keeps
    JSON-object mode plus Amora's local RealizationValidator instead of assuming a
    provider/model-specific strict JSON schema capability.
    """

    async def realize(self, context: CognitiveContext) -> dict[str, object]:
        instructions = (
            render_trusted_protocol(context)
            + "\n\nAMORA REALIZATION CONTRACT\n"
            + realization_instruction(context)
        )
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": _responses_input(context),
            "stream": False,
            "temperature": 0.58,
            "top_p": 0.90,
            "max_output_tokens": 700,
            "text": {"format": {"type": "json_object"}},
        }
        from .context_budget import ensure_payload
        ensure_payload(context, payload)
        root, response, latency_ms = await self._post_responses(payload)
        parsed = _parse_response_json(root, purpose="realization")
        usage = root.get("usage") if isinstance(root.get("usage"), dict) else {}
        parsed["_usage"] = {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "status_code": response.status_code,
            "remaining_requests": _header_int(response, "x-ratelimit-remaining-requests", "ratelimit-remaining"),
            "remaining_tokens": _header_int(response, "x-ratelimit-remaining-tokens"),
            "latency_ms": latency_ms,
            "transport": "responses",
            "instructions_separate": True,
        }
        return parsed

    async def propose(self, context: CognitiveContext) -> dict[str, object]:
        memory_only = is_memory_only(context)
        instructions = memory_instructions() if memory_only else (
            self._system_prompt()
            + _IDENTITY_CONTRACT
            + "\n\n"
            + render_trusted_protocol(context)
            + "\n\n"
            + _REFLECTION_INSTRUCTION
            + " Produza apenas propostas estruturadas sujeitas aos gates locais."
        )
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": [{"role": "user", "content": memory_input(context)}] if memory_only else _responses_input(context),
            "stream": False,
            "temperature": 0.25,
            "top_p": 0.8,
            "max_output_tokens": 1200 if memory_only else 900,
            "text": {"format": {"type": "json_object"}},
        }
        from .context_budget import ensure_payload
        ensure_payload(context, payload)
        root, response, latency_ms = await self._post_responses(payload)
        parsed = _parse_response_json(root, purpose="proposal")
        if memory_only:
            parsed = validate_memory_extraction(parsed)
        usage = root.get("usage") if isinstance(root.get("usage"), dict) else {}
        parsed["_usage"] = {
            "input_tokens": usage.get("input_tokens"),
            "output_tokens": usage.get("output_tokens"),
            "status_code": response.status_code,
            "remaining_requests": _header_int(response, "x-ratelimit-remaining-requests", "ratelimit-remaining"),
            "remaining_tokens": _header_int(response, "x-ratelimit-remaining-tokens"),
            "latency_ms": latency_ms,
            "transport": "responses",
            "instructions_separate": True,
        }
        return parsed

    async def _post_responses(
        self,
        payload: dict[str, object],
    ) -> tuple[dict[str, object], Any, float]:
        started = perf_counter()
        timeout = httpx.Timeout(self.timeout_seconds, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(
                _OPENROUTER_RESPONSES_URL,
                headers=self._headers(),
                json=payload,
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"openrouter Responses request failed ({response.status_code})",
                status_code=response.status_code,
                retry_after=response.headers.get("retry-after"),
            )
        try:
            root = response.json()
        except ValueError as exc:
            raise ProviderError("openrouter Responses returned invalid JSON envelope") from exc
        if not isinstance(root, dict):
            raise ProviderError("openrouter Responses root must be an object")
        return root, response, (perf_counter() - started) * 1000.0


def _parse_response_json(root: dict[str, object], *, purpose: str) -> dict[str, object]:
    content = _response_output_text(root)
    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError, ValueError) as exc:
        raise ProviderError(f"openrouter returned invalid {purpose} JSON") from exc
    if not isinstance(parsed, dict):
        raise ProviderError(f"openrouter {purpose} root must be an object")
    return parsed


def _response_output_text(root: dict[str, object]) -> str:
    direct = root.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    parts: list[str] = []
    for output in root.get("output") or []:
        if not isinstance(output, dict) or output.get("type") != "message":
            continue
        for content in output.get("content") or []:
            if not isinstance(content, dict) or content.get("type") != "output_text":
                continue
            text = content.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts).strip()


def _header_int(response: Any, *names: str) -> int | None:
    for name in names:
        value = response.headers.get(name)
        try:
            return int(float(value)) if value is not None else None
        except (TypeError, ValueError):
            continue
    return None


class ExperimentalVeniceSocialProvider:
    """Operator-only Chat Completions transport. Never registered in routing.

    JSON is requested through the canonical instruction, not an unverified
    response_format capability. Each call is one HTTP request without retries.
    """

    MODEL = "cognitivecomputations/dolphin-mistral-24b-venice-edition:free"
    API_URL = "https://openrouter.ai/api/v1/chat/completions"

    def __init__(self, secrets: Any) -> None:
        self.secrets = secrets

    def payload(self, canonical: dict[str, Any]) -> dict[str, Any]:
        from copy import deepcopy
        return {
            "model": self.MODEL,
            "messages": deepcopy(canonical["messages"]),
            "temperature": canonical["temperature"],
            "top_p": canonical["top_p"],
            "max_tokens": canonical["max_tokens"],
            "stream": False,
            "provider": {"allow_fallbacks": False, "max_price": {"prompt": 0, "completion": 0}},
        }

    async def post_chat(self, payload: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str], int]:
        key = self.secrets.get("OPENROUTER_API_KEY")
        if not key:
            raise ProviderError("Venice experiment secret missing")
        async with httpx.AsyncClient(timeout=httpx.Timeout(185, connect=10), follow_redirects=False) as client:
            response = await client.post(self.API_URL, headers={
                "Authorization": f"Bearer {key}", "Content-Type": "application/json",
            }, json=payload)
        if response.status_code >= 400:
            raise ProviderError("Venice experiment HTTP failure", status_code=response.status_code,
                                retry_after=response.headers.get("retry-after"))
        root = response.json()
        if not isinstance(root, dict) or root.get("error"):
            raise ProviderError("Venice experiment invalid envelope")
        return root, dict(response.headers), response.status_code
