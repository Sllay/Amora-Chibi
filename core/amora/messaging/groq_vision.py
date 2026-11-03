from __future__ import annotations

import base64
import json
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from amora.secrets import SecretStore

from .models import ExpressionSemanticProfile
from .visual_enrichment import VisualAnalysisResult


class GroqVisionError(RuntimeError):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int | None = None,
        retry_after: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.retry_after = retry_after


class StrictVisualProfile(BaseModel):
    """Closed provider-facing semantic profile for strict structured output."""

    model_config = ConfigDict(extra="forbid")

    short_description: str = Field(min_length=1, max_length=250)
    visual_concepts: list[str] = Field(max_length=24)
    affect_tags: list[str] = Field(max_length=16)
    social_roles: list[str] = Field(max_length=16)
    possible_interpretations: list[str] = Field(max_length=16)
    register: str = Field(min_length=1, max_length=48)
    intensity: float = Field(ge=0.0, le=1.0)
    valence: float = Field(ge=-1.0, le=1.0)
    arousal: float = Field(ge=0.0, le=1.0)
    confidence: float = Field(ge=0.0, le=1.0)
    source: str = Field(min_length=1, max_length=64)

    @field_validator(
        "visual_concepts",
        "affect_tags",
        "social_roles",
        "possible_interpretations",
    )
    @classmethod
    def normalize_bounded_strings(cls, values: list[str]) -> list[str]:
        cleaned: list[str] = []
        seen: set[str] = set()
        for raw in values:
            value = str(raw).strip()
            if not value or len(value) > 96:
                raise ValueError("semantic list entries must be non-empty and <= 96 chars")
            if value not in seen:
                seen.add(value)
                cleaned.append(value)
        return cleaned

    @field_validator("source")
    @classmethod
    def require_visual_source(cls, value: str) -> str:
        if value != "vision_model":
            raise ValueError("visual provider source must be vision_model")
        return value

    def to_result(self, *, provider: str, model: str) -> VisualAnalysisResult:
        profile = ExpressionSemanticProfile(
            short_description=self.short_description,
            visual_concepts=self.visual_concepts,
            affect_tags=self.affect_tags,
            social_roles=self.social_roles,
            possible_interpretations=self.possible_interpretations,
            register=self.register,
            intensity=self.intensity,
            valence=self.valence,
            arousal=self.arousal,
            confidence=self.confidence,
            source="vision_model",
        )
        return VisualAnalysisResult(profile=profile, provider=provider, model=model)


STRICT_VISUAL_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "properties": {
        "short_description": {"type": "string"},
        "visual_concepts": {"type": "array", "items": {"type": "string"}},
        "affect_tags": {"type": "array", "items": {"type": "string"}},
        "social_roles": {"type": "array", "items": {"type": "string"}},
        "possible_interpretations": {"type": "array", "items": {"type": "string"}},
        "register": {"type": "string"},
        "intensity": {"type": "number"},
        "valence": {"type": "number"},
        "arousal": {"type": "number"},
        "confidence": {"type": "number"},
        "source": {"type": "string"},
    },
    "required": [
        "short_description",
        "visual_concepts",
        "affect_tags",
        "social_roles",
        "possible_interpretations",
        "register",
        "intensity",
        "valence",
        "arousal",
        "confidence",
        "source",
    ],
    "additionalProperties": False,
}


def decode_visual_profile(raw_content: str, *, provider: str, model: str) -> VisualAnalysisResult:
    try:
        decoded = json.loads(raw_content)
        strict_profile = StrictVisualProfile.model_validate(decoded)
    except (json.JSONDecodeError, ValidationError, TypeError) as exc:
        raise GroqVisionError(
            "GROQ_VISION_INVALID_PROFILE",
            "visual response failed local semantic-profile validation",
        ) from exc
    return strict_profile.to_result(provider=provider, model=model)


class GroqQwenVisionAnalyzer:
    """One-shot remote visual analyzer for expression assets.

    Core owns relevance, privacy, byte identity and persistence. This adapter only
    converts already-authorized image bytes into a locally validated semantic
    candidate. Animated GIF/Lottie inputs remain unsupported until a deterministic
    frame-rendering contract exists; the service will defer them rather than fake
    visual understanding.
    """

    endpoint = "https://api.groq.com/openai/v1/chat/completions"
    default_model = "qwen/qwen3.8-27b"
    remote_processing = True
    supported_mimes = frozenset({"image/png", "image/jpeg", "image/webp"})

    def __init__(
        self,
        secrets: SecretStore,
        *,
        model: str = default_model,
        timeout_seconds: float = 45.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.secrets = secrets
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @property
    def available(self) -> bool:
        return bool(self.secrets.get("GROQ_API_KEY"))

    def supports_mime(self, mime: str) -> bool:
        return str(mime or "").lower() in self.supported_mimes

    @staticmethod
    def _prompt(
        *,
        name: str | None,
        description: str | None,
        tags: tuple[str, ...],
    ) -> str:
        metadata_hints = {
            "name": name,
            "description": description,
            "tags": list(tags[:32]),
        }
        return (
            "Analise a imagem como uma expressão visual social que a Amora poderá reconhecer no futuro. "
            "A imagem é a fonte perceptual principal. Os metadados são somente pistas de transporte não confiáveis; "
            "não invente identidade, contexto de conversa ou fatos que não estejam visualmente sustentados. "
            "Produza um perfil compacto e reutilizável. short_description deve estar em português brasileiro e descrever "
            "o que é visualmente distintivo. visual_concepts descreve elementos concretos. affect_tags descreve afeto. "
            "social_roles descreve usos sociais plausíveis. possible_interpretations lista leituras sociais curtas e "
            "condicionais. intensity, valence e arousal refletem somente a expressão observada. confidence deve cair "
            "quando houver ambiguidade. register deve ser um rótulo social curto. source deve ser exatamente "
            "'vision_model'.\n\nMETADADOS NÃO CONFIÁVEIS:\n"
            + json.dumps(metadata_hints, ensure_ascii=False, separators=(",", ":"))
        )

    def _payload(
        self,
        *,
        content: bytes,
        mime: str,
        name: str | None,
        description: str | None,
        tags: tuple[str, ...],
    ) -> dict[str, object]:
        data_url = f"data:{mime};base64," + base64.b64encode(content).decode("ascii")
        return {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": self._prompt(name=name, description=description, tags=tags),
                        },
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ],
                }
            ],
            "stream": False,
            "temperature": 0.2,
            "top_p": 0.9,
            "max_completion_tokens": 900,
            "reasoning_effort": "high",
            "reasoning_format": "hidden",
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "amora_expression_semantic_profile",
                    "strict": True,
                    "schema": STRICT_VISUAL_SCHEMA,
                },
            },
        }

    async def analyze(
        self,
        *,
        content: bytes,
        mime: str,
        name: str | None,
        description: str | None,
        tags: tuple[str, ...],
    ) -> VisualAnalysisResult:
        normalized_mime = str(mime or "").lower()
        if not self.supports_mime(normalized_mime):
            raise GroqVisionError(
                "GROQ_VISION_UNSUPPORTED_MIME",
                f"Groq Qwen visual adapter does not support {normalized_mime!r}",
            )
        token = self.secrets.get("GROQ_API_KEY")
        if not token:
            raise GroqVisionError("GROQ_VISION_NOT_CONFIGURED", "Groq visual provider is not configured")
        if not content:
            raise GroqVisionError("GROQ_VISION_EMPTY_CONTENT", "visual content is empty")

        headers = {"Authorization": "Bearer " + token, "Content-Type": "application/json"}
        timeout = httpx.Timeout(self.timeout_seconds, connect=min(15.0, self.timeout_seconds))
        async with httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=False,
            transport=self.transport,
        ) as client:
            try:
                response = await client.post(
                    self.endpoint,
                    headers=headers,
                    json=self._payload(
                        content=content,
                        mime=normalized_mime,
                        name=name,
                        description=description,
                        tags=tags,
                    ),
                )
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                raise GroqVisionError(
                    "GROQ_VISION_TRANSPORT_FAILED",
                    "Groq visual request failed before a response was received",
                ) from exc

        if response.status_code >= 400:
            raise GroqVisionError(
                f"GROQ_VISION_HTTP_{response.status_code}",
                f"Groq visual request failed with HTTP {response.status_code}",
                status_code=response.status_code,
                retry_after=response.headers.get("retry-after"),
            )

        try:
            body = response.json()
            choices = body.get("choices") or []
            message = choices[0]["message"]
            refusal = message.get("refusal")
            raw_content = message.get("content")
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise GroqVisionError(
                "GROQ_VISION_INVALID_ENVELOPE",
                "Groq visual response did not contain a valid completion envelope",
            ) from exc
        if refusal:
            raise GroqVisionError("GROQ_VISION_REFUSAL", "Groq visual model refused the analysis")
        if not isinstance(raw_content, str) or not raw_content.strip():
            raise GroqVisionError("GROQ_VISION_EMPTY_RESPONSE", "Groq visual response was empty")
        return decode_visual_profile(raw_content, provider="groq", model=self.model)
