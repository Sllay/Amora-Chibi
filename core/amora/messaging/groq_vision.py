from __future__ import annotations

import json
from typing import Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

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
