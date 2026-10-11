from __future__ import annotations

import json
import os
from time import perf_counter
from typing import AsyncIterator

import httpx

from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.secrets import SecretStore

from .models import CognitiveContext
from .providers import ProviderDelta, ProviderError
from .providers_v1 import (
    _REFLECTION_INSTRUCTION,
    _canonical_state_text,
    _context_text,
    _dialogue_plan_text,
    _dialogue_turns,
    _system_prompt,
)
from .providers import GroqCognitiveProvider as BaseGroqProvider
from .realization import realization_instruction


_TASKS = frozenset(
    {
        TaskClass.SOCIAL_DIALOGUE,
        TaskClass.REASONING,
        TaskClass.SUMMARIZATION,
        TaskClass.CLASSIFICATION,
        TaskClass.TOOL_PLANNING,
    }
)
_SCOPES = frozenset({PrivacyScope.PUBLIC, PrivacyScope.PERSONAL, PrivacyScope.PRIVATE})


class OpenAICompatibleMeshProvider:
    """Small OpenAI-chat compatible adapter used by zero-cost mesh providers.

    Production dialogue always requests JSON and still passes through Amora's
    local RealizationValidator. Reflection output is likewise proposal-only.
    """

    def __init__(
        self,
        secrets: SecretStore,
        *,
        name: str,
        model: str,
        api_url: str,
        api_key_name: str,
        quality: float,
        latency_score: float,
        priority: int,
        context_window_tokens: int | None,
        available: bool,
        headers: dict[str, str] | None = None,
        timeout_seconds: float = 45.0,
        metadata: dict[str, object] | None = None,
    ) -> None:
        self.secrets = secrets
        self.name = name
        self.model = model
        self.api_url = api_url
        self.api_key_name = api_key_name
        self.timeout_seconds = timeout_seconds
        self.extra_headers = dict(headers or {})
        self.descriptor = ProviderDescriptor(
            name=name,
            model=model,
            capabilities=_TASKS,
            quality=quality,
            latency_score=latency_score,
            cost_score=0.0,
            available=available,
            quota_remaining=None,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=_SCOPES,
            supports_streaming=True,
            supports_structured_output=True,
            context_window_tokens=context_window_tokens,
            priority=priority,
            metadata={"endpoint": api_url, "zero_cost_path": True, **(metadata or {})},
        )

    @staticmethod
    def _system_prompt() -> str:
        return _system_prompt(BaseGroqProvider._system_prompt())

    def _messages(self, context: CognitiveContext) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": (
                    self._system_prompt()
                    + "\n\n"
                    + _canonical_state_text(context)
                    + "\n\n"
                    + _dialogue_plan_text(context)
                ),
            }
        ]
        messages.extend({"role": role, "content": text} for role, text in _dialogue_turns(context))
        messages.append({"role": "user", "content": context.user_text})
        return messages

    def _headers(self) -> dict[str, str]:
        key = self.secrets.get(self.api_key_name)
        if not key:
            raise ProviderError(f"{self.api_key_name} is not configured")
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            **self.extra_headers,
        }

    async def _post(self, payload: dict[str, object]) -> tuple[dict[str, object], httpx.Response, float]:
        started = perf_counter()
        timeout = httpx.Timeout(self.timeout_seconds, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(self.api_url, headers=self._headers(), json=payload)
        if response.status_code >= 400:
            raise ProviderError(
                f"{self.name} request failed ({response.status_code}): {response.text[:1000]}",
                status_code=response.status_code,
                retry_after=response.headers.get("retry-after"),
            )
        try:
            root = response.json()
        except ValueError as exc:
            raise ProviderError(f"{self.name} returned invalid JSON envelope") from exc
        if not isinstance(root, dict):
            raise ProviderError(f"{self.name} response root must be an object")
        return root, response, (perf_counter() - started) * 1000.0

    async def realize(self, context: CognitiveContext) -> dict[str, object]:
        messages = self._messages(context)
        messages[0] = {
            **messages[0],
            "content": messages[0]["content"] + "\n\n" + realization_instruction(context),
        }
        root, response, latency_ms = await self._post(
            {
                "model": self.model,
                "messages": messages,
                "stream": False,
                "temperature": 0.58,
                "top_p": 0.90,
                "max_tokens": 700,
                "response_format": {"type": "json_object"},
            }
        )
        parsed = _parse_chat_json(root, provider=self.name, purpose="realization")
        usage = root.get("usage") or {}
        parsed["_usage"] = {
            "input_tokens": usage.get("prompt_tokens") if isinstance(usage, dict) else None,
            "output_tokens": usage.get("completion_tokens") if isinstance(usage, dict) else None,
            "status_code": response.status_code,
            "remaining_requests": _first_header(response, "x-ratelimit-remaining-requests", "ratelimit-remaining"),
            "remaining_tokens": _first_header(response, "x-ratelimit-remaining-tokens"),
            "latency_ms": latency_ms,
        }
        return parsed

    async def propose(self, context: CognitiveContext) -> dict[str, object]:
        root, response, latency_ms = await self._post(
            {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": self._system_prompt() + " " + _REFLECTION_INSTRUCTION},
                    {
                        "role": "user",
                        "content": _context_text(
                            context,
                            instruction="Produza apenas propostas estruturadas sujeitas aos gates locais.",
                        ),
                    },
                ],
                "stream": False,
                "temperature": 0.25,
                "top_p": 0.8,
                "max_tokens": 900,
                "response_format": {"type": "json_object"},
            }
        )
        parsed = _parse_chat_json(root, provider=self.name, purpose="proposal")
        usage = root.get("usage") or {}
        parsed["_usage"] = {
            "input_tokens": usage.get("prompt_tokens") if isinstance(usage, dict) else None,
            "output_tokens": usage.get("completion_tokens") if isinstance(usage, dict) else None,
            "status_code": response.status_code,
            "remaining_requests": _first_header(response, "x-ratelimit-remaining-requests", "ratelimit-remaining"),
            "remaining_tokens": _first_header(response, "x-ratelimit-remaining-tokens"),
            "latency_ms": latency_ms,
        }
        return parsed

    async def stream(self, context: CognitiveContext) -> AsyncIterator[ProviderDelta]:
        """Replay-compatible diagnostic stream; production dialogue uses realize()."""
        root, response, latency_ms = await self._post(
            {
                "model": self.model,
                "messages": self._messages(context),
                "stream": False,
                "temperature": 0.58,
                "top_p": 0.90,
                "max_tokens": 700,
            }
        )
        try:
            content = str(root["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"{self.name} returned invalid chat completion") from exc
        usage = root.get("usage") or {}
        yield ProviderDelta(
            text=content,
            input_tokens=_safe_int(usage.get("prompt_tokens")) if isinstance(usage, dict) else None,
            output_tokens=_safe_int(usage.get("completion_tokens")) if isinstance(usage, dict) else None,
            status_code=response.status_code,
        )
        yield ProviderDelta(done=True, latency_ms=latency_ms, status_code=response.status_code)


class CloudflareCognitiveProvider(OpenAICompatibleMeshProvider):
    """Direct Workers AI adapter; deliberately does not use Unified Billing."""

    DEFAULT_MODEL = "@cf/meta/llama-3.1-8b-instruct-fast"

    def __init__(self, secrets: SecretStore, *, model: str | None = None, timeout_seconds: float = 45.0) -> None:
        account_id = (secrets.get("CLOUDFLARE_ACCOUNT_ID") or "").strip()
        token_present = secrets.get("CLOUDFLARE_API_TOKEN") is not None
        selected = model or os.getenv("AMORA_CLOUDFLARE_COGNITION_MODEL", self.DEFAULT_MODEL)
        api_url = (
            f"https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1/chat/completions"
            if account_id
            else "https://api.cloudflare.com/client/v4/accounts/UNCONFIGURED/ai/v1/chat/completions"
        )
        super().__init__(
            secrets,
            name="cloudflare",
            model=selected,
            api_url=api_url,
            api_key_name="CLOUDFLARE_API_TOKEN",
            quality=0.78,
            latency_score=0.84,
            priority=6,
            context_window_tokens=128_000,
            available=bool(account_id and token_present),
            timeout_seconds=timeout_seconds,
            metadata={
                "service": "workers_ai_direct",
                "billing_path": "workers_ai",
                "unified_billing": False,
            },
        )


class OpenRouterCognitiveProvider(OpenAICompatibleMeshProvider):
    """Last-resort broker restricted to explicitly free model routes by default."""

    DEFAULT_MODEL = "openrouter/free"

    def __init__(self, secrets: SecretStore, *, model: str | None = None, timeout_seconds: float = 45.0) -> None:
        selected = model or os.getenv("AMORA_OPENROUTER_COGNITION_MODEL", self.DEFAULT_MODEL)
        zero_cost = _openrouter_model_is_free(selected)
        key_present = secrets.get("OPENROUTER_API_KEY") is not None
        super().__init__(
            secrets,
            name="openrouter",
            model=selected,
            api_url="https://openrouter.ai/api/v1/chat/completions",
            api_key_name="OPENROUTER_API_KEY",
            quality=0.80,
            latency_score=0.66,
            priority=-2,
            context_window_tokens=64_000,
            available=bool(key_present and zero_cost),
            timeout_seconds=timeout_seconds,
            headers={"HTTP-Referer": "https://github.com/Sllay/Amora-Chibi", "X-Title": "Amora-Chibi"},
            metadata={
                "service": "openrouter",
                "zero_cost_route": zero_cost,
                "broker_last_resort": True,
            },
        )


def _openrouter_model_is_free(model: str) -> bool:
    normalized = model.strip().casefold()
    return normalized == "openrouter/free" or normalized.endswith(":free")


def _parse_chat_json(root: dict[str, object], *, provider: str, purpose: str) -> dict[str, object]:
    try:
        choices = root["choices"]
        content = choices[0]["message"]["content"]  # type: ignore[index]
        parsed = json.loads(str(content))
    except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
        raise ProviderError(f"{provider} returned invalid {purpose} JSON") from exc
    if not isinstance(parsed, dict):
        raise ProviderError(f"{provider} {purpose} root must be an object")
    return parsed


def _first_header(response: httpx.Response, *names: str) -> int | None:
    for name in names:
        value = response.headers.get(name)
        parsed = _safe_int(value)
        if parsed is not None:
            return parsed
    return None


def _safe_int(value: object) -> int | None:
    try:
        return int(float(value)) if value is not None else None
    except (TypeError, ValueError):
        return None
