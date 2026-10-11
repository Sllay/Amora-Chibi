from __future__ import annotations

import json
from time import perf_counter

import httpx

from .models import CognitiveContext
from .providers import ProviderError
from .providers_v1 import GeminiCognitiveProvider as BaseGeminiCognitiveProvider
from .providers_v1 import GroqCognitiveProvider as BaseGroqCognitiveProvider
from .realization import realization_instruction


class GroqCognitiveProvider(BaseGroqCognitiveProvider):
    """Groq V1 adapter with non-streaming structured dialogue realization."""

    async def realize(self, context: CognitiveContext) -> dict[str, object]:
        api_key = self.secrets.get("GROQ_API_KEY")
        if not api_key:
            raise ProviderError("GROQ_API_KEY is not configured")

        messages = self._messages(context)
        messages[0] = {
            **messages[0],
            "content": messages[0]["content"] + "\n\n" + realization_instruction(context),
        }
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": 0.58,
            "top_p": 0.90,
            "max_completion_tokens": 700,
            "response_format": {"type": "json_object"},
        }
        started = perf_counter()
        timeout = httpx.Timeout(self.timeout_seconds, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                self.API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"Groq structured realization failed ({response.status_code}): {response.text[:1000]}",
                status_code=response.status_code,
                retry_after=response.headers.get("retry-after"),
            )
        try:
            root = response.json()
            content = root["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ProviderError("Groq returned invalid realization JSON") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("Groq realization root must be an object")
        usage = root.get("usage") or {}
        parsed["_usage"] = {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "status_code": response.status_code,
            "remaining_requests": response.headers.get("x-ratelimit-remaining-requests"),
            "remaining_tokens": response.headers.get("x-ratelimit-remaining-tokens"),
            "latency_ms": (perf_counter() - started) * 1000.0,
        }
        return parsed


class GeminiCognitiveProvider(BaseGeminiCognitiveProvider):
    """Gemini V1 adapter with JSON realization validated by the local Core."""

    async def realize(self, context: CognitiveContext) -> dict[str, object]:
        key = self.secrets.get("GEMINI_API_KEY")
        if not key:
            raise ProviderError("GEMINI_API_KEY is not configured")

        payload = self._payload(context)
        generation = dict(payload.get("generationConfig") or {})
        generation.update(
            {
                "temperature": 0.58,
                "topP": 0.90,
                "maxOutputTokens": 700,
                "responseMimeType": "application/json",
            }
        )
        payload["generationConfig"] = generation
        system_instruction = payload.get("systemInstruction") or {"parts": [{"text": ""}]}
        parts = system_instruction.get("parts") or [{"text": ""}]
        current = str((parts[0] or {}).get("text") or "")
        parts[0] = {"text": current + "\n\n" + realization_instruction(context)}
        system_instruction["parts"] = parts
        payload["systemInstruction"] = system_instruction

        url = f"{self.API_ROOT}/{self.model}:generateContent"
        started = perf_counter()
        timeout = httpx.Timeout(self.timeout_seconds, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"Gemini structured realization failed ({response.status_code}): {response.text[:1000]}",
                status_code=response.status_code,
                retry_after=response.headers.get("retry-after"),
            )
        try:
            root = response.json()
            parts = root["candidates"][0]["content"]["parts"]
            content = "".join(str(part.get("text") or "") for part in parts)
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ProviderError("Gemini returned invalid realization JSON") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("Gemini realization root must be an object")
        usage = root.get("usageMetadata") or {}
        parsed["_usage"] = {
            "input_tokens": usage.get("promptTokenCount"),
            "output_tokens": usage.get("candidatesTokenCount"),
            "status_code": response.status_code,
            "latency_ms": (perf_counter() - started) * 1000.0,
        }
        return parsed
