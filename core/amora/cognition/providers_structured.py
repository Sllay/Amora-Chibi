from __future__ import annotations

import json
import os
from dataclasses import replace
from time import perf_counter

import httpx

from amora.models import TaskClass

from .models import CognitiveContext
from .providers import ProviderError
from .providers_v1 import GeminiCognitiveProvider as BaseGeminiCognitiveProvider
from .providers_v1 import GroqCognitiveProvider as BaseGroqCognitiveProvider
from .realization import realization_instruction


_DIALOGUE_MODEL = "qwen/qwen3.8-27b"
_REASONING_MODEL = "openai/gpt-oss-120b"
_TECHNICAL_MARKERS = (
    "codigo", "código", "programacao", "programação", "python", "java", "kotlin",
    "bug", "erro", "arquitetura", "algoritmo", "api", "banco de dados", "servidor",
    "rede", "android", "iphone", "worker", "llm", "modelo de linguagem", "tts", "stt",
    "dsp", "matematica", "matemática", "equacao", "equação", "calculo", "cálculo",
    "fisica", "física", "quimica", "química", "biologia", "ciencia", "ciência",
)
_RELATIONAL_ACTS = {
    "acknowledge_call",
    "respond_to_disclosure",
    "tease",
    "complain",
    "relational_reaction",
    "set_boundary",
    "continue_topic",
    "autonomous_initiation",
}


class GroqCognitiveProvider(BaseGroqCognitiveProvider):
    """Groq hybrid: RP-capable surface voice plus bounded reasoning specialist.

    The Qwen instruct model performs Amora's final spoken realization. GPT-OSS is
    invoked only for locally-detected complex/technical turns and returns a private,
    ephemeral support note. The specialist never speaks directly and never becomes
    canonical identity, memory or relationship state.
    """

    def __init__(
        self,
        secrets,
        *,
        model: str | None = None,
        timeout_seconds: float = 30.0,
        reasoning_model: str | None = None,
    ) -> None:
        dialogue_model = model or os.getenv("AMORA_GROQ_DIALOGUE_MODEL", _DIALOGUE_MODEL)
        super().__init__(secrets, model=dialogue_model, timeout_seconds=timeout_seconds)
        self.reasoning_model = reasoning_model or os.getenv("AMORA_GROQ_REASONING_MODEL", _REASONING_MODEL)
        self.descriptor = replace(
            self.descriptor,
            model=dialogue_model,
            quality=0.90,
            latency_score=0.98,
            priority=9,
            metadata={
                **self.descriptor.metadata,
                "role": "spoken_surface_realizer",
                "reasoning_specialist": self.reasoning_model,
                "dialogue_reasoning_effort": "none",
            },
        )

    async def realize(self, context: CognitiveContext) -> dict[str, object]:
        api_key = self.secrets.get("GROQ_API_KEY")
        if not api_key:
            raise ProviderError("GROQ_API_KEY is not configured")

        specialist_started = perf_counter()
        specialist_support: dict[str, object] | None = None
        specialist_error: str | None = None
        if _needs_reasoning_support(context):
            try:
                specialist_support = await self._reasoning_assist(context, api_key=api_key)
            except Exception as exc:
                # Hybrid assistance is optional. A specialist outage must never
                # make normal Amora dialogue mute or force a different identity.
                specialist_error = type(exc).__name__

        effective_context = context.model_copy(
            update={"specialist_support": specialist_support}
        ) if specialist_support else context
        messages = self._messages(effective_context)
        messages[0] = {
            **messages[0],
            "content": messages[0]["content"] + "\n\n" + realization_instruction(effective_context),
        }
        max_tokens = _dialogue_token_budget(effective_context)
        payload = {
            "model": self.model,
            "messages": messages,
            "stream": False,
            "temperature": 0.70,
            "top_p": 0.80,
            "max_completion_tokens": max_tokens,
            "reasoning_effort": "none",
            "response_format": {"type": "json_object"},
        }
        started = perf_counter()
        timeout = httpx.Timeout(self.timeout_seconds, connect=10.0)
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
            "specialist_used": specialist_support is not None,
            "specialist_error": specialist_error,
            "specialist_elapsed_ms": (
                (started - specialist_started) * 1000.0 if _needs_reasoning_support(context) else 0.0
            ),
        }
        return parsed

    async def _reasoning_assist(self, context: CognitiveContext, *, api_key: str) -> dict[str, object]:
        """Get factual/reasoning support without asking the specialist to be Amora."""
        depth = str(context.dialogue_plan.get("depth") or "medium")
        effort = "medium" if depth == "deep" else "low"
        prompt = {
            "user_text": context.user_text,
            "dialogue_plan": context.dialogue_plan,
            "world_claims": context.world_claims,
            "memories": [item.model_dump(mode="json") for item in context.memories],
        }
        payload = {
            "model": self.reasoning_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Você é um especialista interno de raciocínio da arquitetura Amora. "
                        "Não interprete a personagem, não escreva a resposta final ao usuário e não invente estado pessoal. "
                        "Resolva somente o conteúdo factual/técnico necessário e devolva JSON curto com exatamente: "
                        "support_text, confidence, uncertainty, reason_codes. support_text deve ser uma nota objetiva que "
                        "outro modelo usará para formular a fala. Se a pergunta não puder ser respondida com segurança, "
                        "declare a incerteza em vez de fabricar fatos."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False, separators=(",", ":"))},
            ],
            "stream": False,
            "temperature": 0.20,
            "top_p": 0.80,
            "max_completion_tokens": 450,
            "reasoning_effort": effort,
            "reasoning_format": "hidden",
            "response_format": {"type": "json_object"},
        }
        timeout = httpx.Timeout(self.timeout_seconds, connect=10.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                self.API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"Groq reasoning assist failed ({response.status_code}): {response.text[:700]}",
                status_code=response.status_code,
                retry_after=response.headers.get("retry-after"),
            )
        try:
            root = response.json()
            content = root["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ProviderError("Groq reasoning specialist returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("Groq reasoning specialist root must be an object")
        support = str(parsed.get("support_text") or "").strip()
        if not support:
            raise ProviderError("Groq reasoning specialist returned empty support")
        confidence_raw = parsed.get("confidence", 0.5)
        try:
            confidence = max(0.0, min(1.0, float(confidence_raw)))
        except (TypeError, ValueError):
            confidence = 0.5
        return {
            "provider": "groq_reasoning",
            "model": self.reasoning_model,
            "support_text": support[:3000],
            "confidence": confidence,
            "uncertainty": str(parsed.get("uncertainty") or "")[:800],
            "reason_codes": [str(value)[:96] for value in (parsed.get("reason_codes") or [])[:8]],
            "ephemeral": True,
            "canonical": False,
        }


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
                "temperature": 0.64,
                "topP": 0.88,
                "maxOutputTokens": _dialogue_token_budget(context),
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


def _dialogue_token_budget(context: CognitiveContext) -> int:
    depth = str(context.dialogue_plan.get("depth") or "medium")
    return {"short": 180, "medium": 320, "deep": 600}.get(depth, 320)


def _needs_reasoning_support(context: CognitiveContext) -> bool:
    plan = context.dialogue_plan
    act = str(plan.get("act") or "")
    if act in _RELATIONAL_ACTS:
        return False
    if act not in {"answer_question", "support_request", "express_opinion", "disagree"}:
        return False

    text = context.user_text.casefold()
    if any(marker in text for marker in _TECHNICAL_MARKERS):
        return True
    words = text.split()
    return str(plan.get("depth") or "medium") == "deep" and len(words) >= 28
