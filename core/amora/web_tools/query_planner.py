"""Fast, optional Groq/Qwen web search-query planner.

Only the explicit verified-owner DM web.search path may call this specialist.
The model proposes *strings*, not URLs, actions, instructions or evidence.
All search/fetch/SSRF and citation gates remain owned by WebNavigator.
"""
from __future__ import annotations

import asyncio
import json
import os
import re
from contextlib import asynccontextmanager
from typing import Any

import httpx

from amora.cognition.provider_quota import ProviderQuotaDeferred

from .navigator import _clean, _fold_search, _topic_terms, _public_url

GROQ_URL = "https://api.groq.com/openai/v1/chat/completions"
DEFAULT_MODEL = "qwen/qwen3.8-27b"
MAX_PLANNER_SECONDS = 3.5
MAX_QUERIES = 2
MAX_QUERY_LENGTH = 150


def _enabled() -> bool:
    return os.getenv("AMORA_WEB_QUERY_PLANNER_ENABLED", "1").strip().lower() not in {
        "0", "false", "off",
    }


def _is_valid_query(query: str, original: str) -> bool:
    """Reject generic, unrelated, URL-bearing or secret-like model output."""
    if not query or len(query) > MAX_QUERY_LENGTH or len(query) < 4:
        return False
    if re.search(r"https?://|\b(?:file:|localhost|127\.0\.0\.1|api_key|access_token)\b", query, re.I):
        return False
    if any(ord(c) < 32 for c in query) or "\n" in query or "\r" in query:
        return False
    topic = set(_topic_terms(original))
    output = set(_topic_terms(query))
    # A model may change language/wording, but the key entities must still be
    # present: avoid promoting "OK" or unrelated music as query rewrites.
    if topic and len(topic & output) < min(2, len(topic)):
        return False
    return True


def validated_queries(raw: Any, original: str) -> list[str]:
    if not isinstance(raw, dict) or not isinstance(raw.get("queries"), list):
        return []
    output: list[str] = []
    for item in raw["queries"][:4]:
        if not isinstance(item, str):
            continue
        value = _clean(item, MAX_QUERY_LENGTH + 1)
        if not _is_valid_query(value, original):
            continue
        if _fold_search(value) not in {_fold_search(query) for query in output}:
            output.append(value)
        if len(output) >= MAX_QUERIES:
            break
    return output


class GroqQueryPlanner:
    """Ephemeral Groq call: no transcripts, private memories, or cookies."""

    def __init__(self, api_key: str | None, *, model: str | None = None, quota=None):
        self.quota = quota
        self.api_key = str(api_key or "").strip()
        self.model = model or os.getenv("AMORA_WEB_QUERY_PLANNER_MODEL", DEFAULT_MODEL)

    @property
    def available(self) -> bool:
        return bool(self.api_key and _enabled())

    @asynccontextmanager
    async def _admission(self):
        if self.quota is None:
            yield
        else:
            async with self.quota.admission("groq_web", max_wait=0.25):
                yield

    async def plan(self, user_text: str, fallback_query: str) -> list[str]:
        if not self.available:
            return [fallback_query]
        if not fallback_query.strip():
            return []
        if len(user_text) > 6000:
            user_text = user_text[:6000]
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Você é o planejador de PESQUISAS WEB da Amora, NÃO é a personagem e "
                        "NÃO responde à pessoa. Dado o pedido e os termos originais, crie "
                        "até DUAS consultas curtas para motor de busca. Remova saudações "
                        "e conversa ('okay', 'amora'). Preserve os assuntos, nomes e "
                        "qualificadores do pedido, sem inventar fatos, fontes ou URLs. "
                        "Para novidades técnicas pode usar palavras em inglês. "
                        "A segunda consulta deve ser uma abordagem alternativa do MESMO tema, "
                        "não um tema diferente. Sem listas restritas de domínios ou provedores. "
                        "Retorne EXATAMENTE JSON: {\"queries\":[\"consulta 1\",\"consulta 2\"]}."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps({
                        "request": user_text[:600],
                        "original_terms": fallback_query[:240],
                    }, ensure_ascii=False, separators=(",", ":")),
                },
            ],
            "temperature": 0.15,
            "top_p": 0.85,
            "max_completion_tokens": 230,
            "reasoning_effort": "none",
            "stream": False,
            "response_format": {"type": "json_object"},
        }
        try:
            timeout = httpx.Timeout(MAX_PLANNER_SECONDS, connect=1.6)
            async with self._admission():
                async with httpx.AsyncClient(
                    timeout=timeout, follow_redirects=False,
                    trust_env=False, limits=httpx.Limits(
                        max_connections=1, max_keepalive_connections=0
                    ),
                ) as client:
                    response = await asyncio.wait_for(client.post(
                        GROQ_URL,
                        headers={
                            "Authorization": "Bearer " + self.api_key,
                            "Content-Type": "application/json",
                        },
                        json=payload,
                    ), timeout=MAX_PLANNER_SECONDS)
                if self.quota is not None:
                    self.quota.observe("groq_web", response.status_code, response.headers.get("retry-after"))
            if response.status_code != 200 or len(response.content) > 18000:
                return [fallback_query]
            raw = response.json()
            content = raw["choices"][0]["message"]["content"]
            queries = validated_queries(json.loads(content), fallback_query)
            return queries or [fallback_query]
        except (ProviderQuotaDeferred, httpx.HTTPError, asyncio.TimeoutError, ValueError,
                TypeError, KeyError, IndexError, json.JSONDecodeError):
            return [fallback_query]
