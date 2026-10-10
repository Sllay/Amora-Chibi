from __future__ import annotations

import json

import httpx

from .models import CognitiveContext
from .providers import GroqCognitiveProvider as BaseGroqCognitiveProvider, ProviderError


class GroqCognitiveProvider(BaseGroqCognitiveProvider):
    @staticmethod
    def _system_prompt() -> str:
        return (
            BaseGroqCognitiveProvider._system_prompt()
            + " Quando reason_codes contiver AUTONOMOUS_INITIATION, a linha de user_text não é uma fala recebida: "
            "é uma intenção interna da Amora para iniciar contato. Nesse modo, formule uma abertura espontânea coerente "
            "com intenção, memória, relação, humor e afeto, sem fingir que a outra pessoa acabou de dizer algo."
            + " Quando reason_codes contiver COGNITIVE_REFLECTION, não produza fala social casual; trate a solicitação "
            "como reflexão interna estruturada conforme a instrução específica do turno."
        )

    async def propose(self, context: CognitiveContext) -> dict[str, object]:
        """Return bounded structured proposals; never canonical state mutations."""
        api_key = self.secrets.get("GROQ_API_KEY")
        if not api_key:
            raise ProviderError("GROQ_API_KEY is not configured")
        schema_instruction = (
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
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": self._system_prompt() + " " + schema_instruction},
                {
                    "role": "user",
                    "content": (
                        "ESTADO CANÔNICO SELECIONADO:\n"
                        + json.dumps(self._context_payload(context), ensure_ascii=False, separators=(",", ":"))
                        + "\n\nENTRADA/OBJETIVO DO TURNO:\n"
                        + context.user_text
                        + "\n\nProduza apenas propostas estruturadas sujeitas aos gates locais."
                    ),
                },
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
