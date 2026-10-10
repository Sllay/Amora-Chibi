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
from .providers import GroqCognitiveProvider as BaseGroqCognitiveProvider, ProviderDelta, ProviderError


_REFLECTION_INSTRUCTION = (
    "Retorne SOMENTE um objeto JSON válido, sem markdown. O objeto deve conter exatamente estas chaves: "
    "memory_candidates, belief_hypotheses, action_suggestions, tone. "
    "memory_candidates é uma lista de no máximo 6 objetos com kind (episodic|semantic|relational), content, "
    "summary opcional, confidence, importance, novelty, emotional_weight, relationship_relevance, "
    "future_consequence, goal_relevance, entity_refs e tags. "
    "belief_hypotheses é uma lista de no máximo 6 objetos com statement, subject_id, predicate, object_value, "
    "confidence menor que 0.90 e context_key. Nunca proponha belief canônico. "
    "action_suggestions é uma lista de no máximo 6 objetos com action_kind, target_id, urgency, confidence e "
    "reason_code. Sugestões não serão executadas diretamente. "
    "tone é um objeto numérico pequeno. Não inclua raciocínio interno, chain-of-thought ou explicações."
)

_DIALOGUE_CONTRACT = (
    " Amora é o próprio nome da pessoa que fala por esta saída. Trate 'Amora' no início ou isolado como vocativo/chamada "
    "dirigida a ela, nunca como pedido de permissão para chamá-la por esse nome. Se a entrada for apenas uma chamada curta "
    "pelo nome, responda como alguém que foi chamado, de modo breve e natural. Não use fórmulas de atendimento como "
    "'como posso ajudar hoje?', 'em que posso ajudar?', 'estou à disposição' ou equivalentes, salvo se o conteúdo do turno "
    "realmente exigir oferecer ajuda. Não repita automaticamente uma formulação recente da própria Amora só porque ela "
    "aparece em recent_messages. Quando o estado canônico trouxer uma relação familiar ou afetiva com a pessoa, deixe isso "
    "aparecer de forma espontânea quando couber, sem inventar vínculo nem forçar vocativos em toda resposta."
)


def _system_prompt(base: str) -> str:
    return (
        base
        + _DIALOGUE_CONTRACT
        + " Quando reason_codes contiver AUTONOMOUS_INITIATION, a linha de user_text não é uma fala recebida: "
        "é uma intenção interna da Amora para iniciar contato. Nesse modo, formule uma abertura espontânea coerente "
        "com intenção, memória, relação, humor e afeto, sem fingir que a outra pessoa acabou de dizer algo."
        + " Quando reason_codes contiver COGNITIVE_REFLECTION, não produza fala social casual; trate a solicitação "
        "como reflexão interna estruturada conforme a instrução específica do turno."
    )


def _context_text(context: CognitiveContext, *, instruction: str) -> str:
    return (
        "ESTADO CANÔNICO SELECIONADO:\n"
        + json.dumps(BaseGroqCognitiveProvider._context_payload(context), ensure_ascii=False, separators=(",", ":"))
        + "\n\nENTRADA/OBJETIVO DO TURNO:\n"
        + context.user_text
        + "\n\n"
        + instruction
    )


class GroqCognitiveProvider(BaseGroqCognitiveProvider):
    @staticmethod
    def _system_prompt() -> str:
        return _system_prompt(BaseGroqCognitiveProvider._system_prompt())

    async def propose(self, context: CognitiveContext) -> dict[str, object]:
        """Return bounded structured proposals; never canonical state mutations."""
        api_key = self.secrets.get("GROQ_API_KEY")
        if not api_key:
            raise ProviderError("GROQ_API_KEY is not configured")
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt() + " " + _REFLECTION_INSTRUCTION},
                {"role": "user", "content": _context_text(context, instruction="Produza apenas propostas estruturadas sujeitas aos gates locais.")},
            ],
            "temperature": 0.25,
            "top_p": 0.8,
            "max_completion_tokens": 900,
            "response_format": {"type": "json_object"},
        }
        timeout = httpx.Timeout(self.timeout_seconds, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                self.API_URL,
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json=payload,
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"Groq cognitive proposal request failed ({response.status_code}): {response.text[:1000]}",
                status_code=response.status_code,
                retry_after=response.headers.get("retry-after"),
            )
        try:
            root = response.json()
            content = root["choices"][0]["message"]["content"]
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ProviderError("Groq returned invalid structured cognitive proposals") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("Groq structured cognitive proposal root must be an object")
        usage = root.get("usage") or {}
        parsed["_usage"] = {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "status_code": response.status_code,
            "remaining_requests": response.headers.get("x-ratelimit-remaining-requests"),
            "remaining_tokens": response.headers.get("x-ratelimit-remaining-tokens"),
        }
        return parsed


class GeminiCognitiveProvider:
    """Google Gemini fallback for dialogue and gated reflection.

    This adapter deliberately uses the generateContent REST family because it is
    available without another SDK dependency and supports both SSE streaming and
    JSON response MIME configuration.
    """

    API_ROOT = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(
        self,
        secrets: SecretStore,
        *,
        model: str | None = None,
        timeout_seconds: float = 45.0,
    ) -> None:
        self.secrets = secrets
        self.model = model or os.getenv("AMORA_GEMINI_COGNITION_MODEL", "gemini-2.5-flash-lite")
        self.timeout_seconds = timeout_seconds
        self.descriptor = ProviderDescriptor(
            name="gemini",
            model=self.model,
            capabilities=frozenset(
                {
                    TaskClass.SOCIAL_DIALOGUE,
                    TaskClass.REASONING,
                    TaskClass.SUMMARIZATION,
                    TaskClass.CLASSIFICATION,
                    TaskClass.TOOL_PLANNING,
                }
            ),
            quality=0.86,
            latency_score=0.82,
            cost_score=0.15,
            available=self.secrets.get("GEMINI_API_KEY") is not None,
            quota_remaining=None,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PUBLIC, PrivacyScope.PERSONAL, PrivacyScope.PRIVATE}),
            supports_streaming=True,
            supports_structured_output=True,
            context_window_tokens=1_000_000,
            priority=4,
            metadata={"endpoint_family": self.API_ROOT},
        )

    @property
    def available(self) -> bool:
        return self.secrets.get("GEMINI_API_KEY") is not None

    @staticmethod
    def _system_prompt() -> str:
        return _system_prompt(BaseGroqCognitiveProvider._system_prompt())

    def _payload(self, context: CognitiveContext, *, json_output: bool = False) -> dict[str, object]:
        generation: dict[str, object] = {
            "temperature": 0.78 if not json_output else 0.25,
            "topP": 0.92 if not json_output else 0.8,
            "maxOutputTokens": 900 if json_output else 700,
        }
        if json_output:
            generation["responseMimeType"] = "application/json"
        instruction = (
            _REFLECTION_INSTRUCTION + " Produza apenas propostas estruturadas sujeitas aos gates locais."
            if json_output
            else "Gere apenas a resposta falável da Amora."
        )
        return {
            "systemInstruction": {"parts": [{"text": self._system_prompt()}]},
            "contents": [{"role": "user", "parts": [{"text": _context_text(context, instruction=instruction)}]}],
            "generationConfig": generation,
        }

    async def stream(self, context: CognitiveContext) -> AsyncIterator[ProviderDelta]:
        key = self.secrets.get("GEMINI_API_KEY")
        if not key:
            raise ProviderError("GEMINI_API_KEY is not configured")
        url = f"{self.API_ROOT}/{self.model}:streamGenerateContent?alt=sse"
        started = perf_counter()
        timeout = httpx.Timeout(self.timeout_seconds, connect=15.0)
        last_input: int | None = None
        last_output: int | None = None
        finish_reason: str | None = None
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=self._payload(context),
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:1000]
                    raise ProviderError(
                        f"Gemini cognitive request failed ({response.status_code}): {body}",
                        status_code=response.status_code,
                        retry_after=response.headers.get("retry-after"),
                    )
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    for candidate in obj.get("candidates") or []:
                        finish_reason = candidate.get("finishReason") or finish_reason
                        content = candidate.get("content") or {}
                        for part in content.get("parts") or []:
                            text = part.get("text")
                            if text:
                                yield ProviderDelta(text=str(text), status_code=response.status_code)
                    usage = obj.get("usageMetadata") or {}
                    last_input = _safe_int(usage.get("promptTokenCount")) or last_input
                    last_output = _safe_int(usage.get("candidatesTokenCount")) or last_output
                if last_input is not None or last_output is not None:
                    yield ProviderDelta(
                        input_tokens=last_input,
                        output_tokens=last_output,
                        status_code=response.status_code,
                    )
                yield ProviderDelta(
                    done=True,
                    finish_reason=finish_reason,
                    latency_ms=(perf_counter() - started) * 1000.0,
                    status_code=response.status_code,
                )

    async def propose(self, context: CognitiveContext) -> dict[str, object]:
        key = self.secrets.get("GEMINI_API_KEY")
        if not key:
            raise ProviderError("GEMINI_API_KEY is not configured")
        url = f"{self.API_ROOT}/{self.model}:generateContent"
        timeout = httpx.Timeout(self.timeout_seconds, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.post(
                url,
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=self._payload(context, json_output=True),
            )
        if response.status_code >= 400:
            raise ProviderError(
                f"Gemini cognitive proposal request failed ({response.status_code}): {response.text[:1000]}",
                status_code=response.status_code,
                retry_after=response.headers.get("retry-after"),
            )
        try:
            root = response.json()
            parts = root["candidates"][0]["content"]["parts"]
            content = "".join(str(part.get("text") or "") for part in parts)
            parsed = json.loads(content)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ProviderError("Gemini returned invalid structured cognitive proposals") from exc
        if not isinstance(parsed, dict):
            raise ProviderError("Gemini structured cognitive proposal root must be an object")
        usage = root.get("usageMetadata") or {}
        parsed["_usage"] = {
            "input_tokens": usage.get("promptTokenCount"),
            "output_tokens": usage.get("candidatesTokenCount"),
            "status_code": response.status_code,
        }
        return parsed


def _safe_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
