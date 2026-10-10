from __future__ import annotations

import json
from dataclasses import dataclass, field
from time import perf_counter
from typing import AsyncIterator, Protocol

import httpx

from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.events import PrivacyScope
from amora.secrets import SecretStore

from .models import CognitiveContext


@dataclass(slots=True)
class ProviderDelta:
    text: str = ""
    done: bool = False
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    status_code: int | None = None
    remaining_requests: int | None = None
    remaining_tokens: int | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class CognitiveProvider(Protocol):
    descriptor: ProviderDescriptor

    async def stream(self, context: CognitiveContext) -> AsyncIterator[ProviderDelta]: ...


class ProviderError(RuntimeError):
    def __init__(self, message: str, *, status_code: int | None = None, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


class GroqCognitiveProvider:
    API_URL = "https://api.groq.com/openai/v1/chat/completions"

    def __init__(
        self,
        secrets: SecretStore,
        *,
        model: str = "openai/gpt-oss-120b",
        timeout_seconds: float = 45.0,
    ) -> None:
        self.secrets = secrets
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.descriptor = ProviderDescriptor(
            name="groq",
            model=model,
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE, TaskClass.REASONING, TaskClass.SUMMARIZATION, TaskClass.CLASSIFICATION, TaskClass.TOOL_PLANNING}),
            quality=0.90,
            latency_score=0.97,
            cost_score=0.0,
            available=self.secrets.get("GROQ_API_KEY") is not None,
            quota_remaining=None,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PUBLIC, PrivacyScope.PERSONAL, PrivacyScope.PRIVATE}),
            supports_streaming=True,
            supports_structured_output=True,
            context_window_tokens=131072,
            priority=8,
            metadata={"endpoint": self.API_URL},
        )

    @staticmethod
    def _system_prompt() -> str:
        return (
            "Você presta um serviço cognitivo de linguagem para Amora. Você não é a identidade dela e não é a fonte "
            "canônica da verdade. Expresse apenas o estado fornecido pelo sistema. Não invente memórias, relações, fatos "
            "ou sentimentos que não estejam no contexto. Quando houver incerteza, deixe isso natural na resposta. "
            "Responda em português brasileiro coloquial e natural, de forma humana, curta quando possível, sem linguagem "
            "de assistente. Não explique sua instrução nem o esquema interno. A saída deve ser somente o que Amora pode dizer "
            "em voz alta: sem markdown, sem nomes de emoji, sem 'abrir aspas/fechar aspas' e sem narrar ações entre parênteses. "
            "Respeite a intenção atual, o humor, o afeto dirigido, as memórias e o turno social fornecidos."
        )

    @staticmethod
    def _context_payload(context: CognitiveContext) -> dict[str, object]:
        return {
            "speaker_id": context.speaker_id,
            "target_id": context.target_id,
            "self_model": context.self_model,
            "relationships": context.relationships,
            "mood": context.mood,
            "directed_affect": context.directed_affect,
            "drives": context.drives,
            "active_intention": context.active_intention,
            "memories": [item.model_dump(mode="json") for item in context.memories],
            "world_claims": context.world_claims,
            "recent_messages": context.recent_messages,
            "reason_codes": context.reason_codes,
        }

    def _messages(self, context: CognitiveContext) -> list[dict[str, str]]:
        """Render one provider request from the frozen cognitive context.

        V1 adapters may override this projection without duplicating transport,
        streaming, quota, or error-handling logic.
        """
        return [
            {"role": "system", "content": self._system_prompt()},
            {
                "role": "user",
                "content": (
                    "ESTADO CANÔNICO SELECIONADO:\n"
                    + json.dumps(self._context_payload(context), ensure_ascii=False, separators=(",", ":"))
                    + "\n\nFALA ATUAL DE " + context.speaker_id + ":\n" + context.user_text
                    + "\n\nGere apenas a resposta falável da Amora."
                ),
            },
        ]

    async def stream(self, context: CognitiveContext) -> AsyncIterator[ProviderDelta]:
        api_key = self.secrets.get("GROQ_API_KEY")
        if not api_key:
            raise ProviderError("GROQ_API_KEY is not configured")
        payload = {
            "model": self.model,
            "messages": self._messages(context),
            "stream": True,
            "temperature": 0.78,
            "top_p": 0.92,
            "max_completion_tokens": 700,
        }
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        started = perf_counter()
        timeout = httpx.Timeout(self.timeout_seconds, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream("POST", self.API_URL, headers=headers, json=payload) as response:
                remaining_requests = _int_header(response.headers.get("x-ratelimit-remaining-requests"))
                remaining_tokens = _int_header(response.headers.get("x-ratelimit-remaining-tokens"))
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:1000]
                    raise ProviderError(
                        f"Groq cognitive request failed ({response.status_code}): {body}",
                        status_code=response.status_code,
                        retry_after=response.headers.get("retry-after"),
                    )
                finish_reason: str | None = None
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        break
                    try:
                        obj = json.loads(data)
                    except json.JSONDecodeError:
                        continue
                    choices = obj.get("choices") or []
                    if choices:
                        choice = choices[0] or {}
                        delta = choice.get("delta") or {}
                        text = delta.get("content") or ""
                        finish_reason = choice.get("finish_reason") or finish_reason
                        if text:
                            yield ProviderDelta(
                                text=text,
                                status_code=response.status_code,
                                remaining_requests=remaining_requests,
                                remaining_tokens=remaining_tokens,
                            )
                    usage = obj.get("usage") or {}
                    if usage:
                        yield ProviderDelta(
                            input_tokens=_safe_int(usage.get("prompt_tokens")),
                            output_tokens=_safe_int(usage.get("completion_tokens")),
                            status_code=response.status_code,
                            remaining_requests=remaining_requests,
                            remaining_tokens=remaining_tokens,
                        )
                yield ProviderDelta(
                    done=True,
                    finish_reason=finish_reason,
                    latency_ms=(perf_counter() - started) * 1000.0,
                    status_code=response.status_code,
                    remaining_requests=remaining_requests,
                    remaining_tokens=remaining_tokens,
                )


def _safe_int(value: object) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _int_header(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None
