from __future__ import annotations

from typing import Any

from .mesh_providers import (
    CloudflareCognitiveProvider as BaseCloudflareCognitiveProvider,
    OpenRouterCognitiveProvider as BaseOpenRouterCognitiveProvider,
)
from .models import CognitiveContext
from .prompt_frame import render_conversation_turn, render_current_turn, render_provider_frame
from .providers_realtime import GroqCognitiveProvider as BaseGroqCognitiveProvider
from .providers_structured import GeminiCognitiveProvider as BaseGeminiCognitiveProvider
from .providers_v1 import _REFLECTION_INSTRUCTION


_IDENTITY_CONTRACT = (
    "\n\nPROTOCOLO DE IDENTIDADE SOCIAL: os papéis técnicos user/model/assistant pertencem ao protocolo do provider "
    "e NÃO identificam uma pessoa. A identidade social de cada fala é definida exclusivamente por speaker_id e "
    "addressee_id dentro de AMORA CONVERSATION TURN e AMORA PROVIDER FRAME. Mensagens role=user diferentes podem "
    "vir de pessoas diferentes. Nunca funda, renomeie ou troque participantes por semelhança textual, voz, apelido "
    "ou posição no histórico. Quando speaker_id for desconhecido ou temporário, preserve essa incerteza; não adivinhe "
    "uma pessoa canônica."
)


class _IdentityAwareOpenAIMessages:
    def _messages(self, context: CognitiveContext) -> list[dict[str, str]]:
        messages: list[dict[str, str]] = [
            {
                "role": "system",
                "content": self._system_prompt() + _IDENTITY_CONTRACT + "\n\n" + render_provider_frame(context),
            }
        ]
        messages.extend(
            {"role": role, "content": content}
            for role, content in _identity_history(context)
        )
        messages.append({"role": "user", "content": render_current_turn(context)})
        return messages


class GroqCognitiveProvider(_IdentityAwareOpenAIMessages, BaseGroqCognitiveProvider):
    """Production Groq adapter with explicit multi-speaker turn identity."""


class CloudflareCognitiveProvider(_IdentityAwareOpenAIMessages, BaseCloudflareCognitiveProvider):
    """Cloudflare mesh adapter using the same provider-frame identity protocol."""


class OpenRouterCognitiveProvider(_IdentityAwareOpenAIMessages, BaseOpenRouterCognitiveProvider):
    """OpenRouter mesh adapter using the same provider-frame identity protocol."""


class GeminiCognitiveProvider(BaseGeminiCognitiveProvider):
    """Gemini adapter with identity-aware history for dialogue and reflection."""

    def _payload(self, context: CognitiveContext, *, json_output: bool = False) -> dict[str, object]:
        generation: dict[str, object] = {
            "temperature": 0.78 if not json_output else 0.25,
            "topP": 0.92 if not json_output else 0.8,
            "maxOutputTokens": 900 if json_output else 700,
        }
        system_text = self._system_prompt() + _IDENTITY_CONTRACT + "\n\n" + render_provider_frame(context)

        if json_output:
            generation["responseMimeType"] = "application/json"
            return {
                "systemInstruction": {
                    "parts": [{"text": system_text + " " + _REFLECTION_INSTRUCTION}]
                },
                "contents": [
                    {
                        "role": "user",
                        "parts": [
                            {
                                "text": (
                                    render_current_turn(context)
                                    + "\n\nProduza apenas propostas estruturadas sujeitas aos gates locais."
                                )
                            }
                        ],
                    }
                ],
                "generationConfig": generation,
            }

        contents: list[dict[str, object]] = []
        for role, content in _identity_history(context):
            contents.append(
                {
                    "role": "model" if role == "assistant" else "user",
                    "parts": [{"text": content}],
                }
            )
        contents.append(
            {
                "role": "user",
                "parts": [{"text": render_current_turn(context)}],
            }
        )
        return {
            "systemInstruction": {"parts": [{"text": system_text}]},
            "contents": contents,
            "generationConfig": generation,
        }


def _identity_history(context: CognitiveContext) -> list[tuple[str, str]]:
    """Project literal history while retaining the Core-owned identity envelope.

    Multiple Amora speech segments from one cognitive turn remain grouped as one
    assistant turn. Human/platform turns are grouped only when turn_id and
    speaker_id both match, so two people can never collapse into one chat role.
    """

    grouped: list[dict[str, Any]] = []
    current_turn = str(context.turn_id)
    for item in context.recent_messages:
        turn_id = str(item.get("turn_id") or "")
        if turn_id == current_turn:
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        speaker_id = str(item.get("speaker_id") or "") or None
        addressee_id = str(item.get("addressee_id") or "") or None
        role = "assistant" if speaker_id == "ENTITY_AMORA" else "user"
        source = str(item.get("source") or "")

        if (
            grouped
            and grouped[-1]["role"] == role
            and grouped[-1]["turn_id"] == turn_id
            and grouped[-1]["speaker_id"] == speaker_id
            and grouped[-1]["addressee_id"] == addressee_id
        ):
            grouped[-1]["text"] = f"{grouped[-1]['text']} {text}".strip()
            continue

        grouped.append(
            {
                "role": role,
                "turn_id": turn_id,
                "speaker_id": speaker_id,
                "addressee_id": addressee_id,
                "source": source,
                "text": text,
            }
        )

    return [
        (
            str(item["role"]),
            render_conversation_turn(
                turn_id=item["turn_id"],
                speaker_id=item["speaker_id"],
                addressee_id=item["addressee_id"],
                source=item["source"],
                text=str(item["text"]),
            ),
        )
        for item in grouped
    ]
