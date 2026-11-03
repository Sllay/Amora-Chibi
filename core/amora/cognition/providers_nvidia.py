from __future__ import annotations

import json
import os
from time import perf_counter
from typing import Any

import httpx

from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.secrets import SecretStore

from .models import CognitiveContext
from .prompt_frame import render_current_turn, render_trusted_protocol
from .providers import GroqCognitiveProvider as BaseLanguageProvider
from .providers import ProviderError
from .providers_identity import _identity_history
from .providers_structured import _dialogue_token_budget
from .realization import realization_instruction


_NVIDIA_CHAT_URL = "https://integrate.api.nvidia.com/v1/chat/completions"
_MISTRAL_LARGE_4_MODEL = "mistralai/mistral-large-4-1t-a52b"
# Mistral reasoning tokens share max_tokens with the visible structured answer.
_MISTRAL_COMPLETION_TOKEN_FLOOR = 4096


class NvidiaMistralLarge4Provider:
    """CH3 certification-only social realizer for NVIDIA-hosted Mistral Large 4.

    This adapter is intentionally *not* registered by ``AmoraCore``. CH3 first
    measures it against the current Qwen surface actor with immutable Core
    contexts. Promotion, priority and fallback order are separate decisions made
    only after certification evidence exists.

    NVIDIA's current chat-completions contract is OpenAI-shaped, but CH3 does not
    assume endpoint-level strict JSON Schema support merely because the underlying
    model advertises structured output capability. The provider therefore asks for
    the canonical realization object in trusted instructions and relies on the
    local ``RealizationValidator`` as the acceptance boundary.
    """

    API_URL = _NVIDIA_CHAT_URL

    def __init__(
        self,
        secrets: SecretStore,
        *,
        model: str | None = None,
        timeout_seconds: float = 45.0,
    ) -> None:
        self.secrets = secrets
        self.model = model or os.getenv("AMORA_NVIDIA_MISTRAL_MODEL", _MISTRAL_LARGE_4_MODEL)
        self.timeout_seconds = timeout_seconds
        self.descriptor = ProviderDescriptor(
            name="nvidia_mistral_large_4_candidate",
            model=self.model,
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=0.0,
            latency_score=0.0,
            cost_score=0.0,
            available=self.secrets.get("NVIDIA_API_KEY") is not None,
            quota_remaining=None,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset(
                {PrivacyScope.PUBLIC, PrivacyScope.PERSONAL, PrivacyScope.PRIVATE}
            ),
            supports_streaming=False,
            # Fail closed until the concrete NVIDIA endpoint contract has been
            # proved with strict schema mode in an integration test.
            supports_structured_output=False,
            context_window_tokens=262_144,
            priority=-100,
            metadata={
                "role": "certification_only_social_candidate",
                "endpoint": self.API_URL,
                "promotion_status": "not_promoted",
                "structured_output_status": "endpoint_unverified",
                "reasoning_effort": "high",
            },
        )

    async def realize(self, context: CognitiveContext) -> dict[str, object]:
        api_key = self.secrets.get("NVIDIA_API_KEY")
        if not api_key:
            raise ProviderError("NVIDIA_API_KEY is not configured")

        payload = self._payload(context)
        started = perf_counter()
        root, response_headers, status_code = await self._post_chat(payload, api_key=api_key)
        try:
            content = root["choices"][0]["message"]["content"]
            if not isinstance(content, str):
                raise TypeError("message content is not a string")
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ProviderError("NVIDIA Mistral returned invalid realization JSON") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("NVIDIA Mistral realization root must be an object")

        usage = root.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        parsed["_usage"] = {
            "input_tokens": _optional_int(usage.get("prompt_tokens")),
            "output_tokens": _optional_int(usage.get("completion_tokens")),
            "status_code": status_code,
            "latency_ms": (perf_counter() - started) * 1000.0,
            "remaining_requests": response_headers.get("x-ratelimit-remaining-requests"),
            "remaining_tokens": response_headers.get("x-ratelimit-remaining-tokens"),
            "transport": "nvidia_chat_completions",
            "certification_only": True,
        }
        return parsed

    def _payload(self, context: CognitiveContext) -> dict[str, object]:
        trusted = (
            BaseLanguageProvider._system_prompt()
            + "\n\n"
            + render_trusted_protocol(context)
            + "\n\n"
            + realization_instruction(context)
        )
        messages: list[dict[str, str]] = [{"role": "system", "content": trusted}]
        messages.extend(
            {"role": role, "content": content}
            for role, content in _identity_history(context)
        )
        messages.append({"role": "user", "content": render_current_turn(context)})
        return {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": 0.70,
            "top_p": 0.80,
            # High reasoning is supported across published NVIDIA Mistral 4 contracts;
            # some NIM runtimes reject "none" with HTTP 400.
            "max_tokens": max(_MISTRAL_COMPLETION_TOKEN_FLOOR, _dialogue_token_budget(context)),
            "reasoning_effort": "high",
        }

    async def _post_chat(
        self,
        payload: dict[str, object],
        *,
        api_key: str,
    ) -> tuple[dict[str, Any], dict[str, str], int]:
        timeout = httpx.Timeout(self.timeout_seconds, connect=min(10.0, self.timeout_seconds))
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            response = await client.post(
                self.API_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                },
                json=payload,
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"NVIDIA Mistral realization failed ({response.status_code}): {response.text[:800]}",
                status_code=response.status_code,
                retry_after=response.headers.get("retry-after"),
            )
        try:
            root = response.json()
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ProviderError("NVIDIA Mistral returned invalid HTTP JSON") from exc
        if not isinstance(root, dict):
            raise ProviderError("NVIDIA Mistral HTTP response root must be an object")
        return root, dict(response.headers), response.status_code


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
