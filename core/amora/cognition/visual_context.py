"""Bounded per-turn image evidence from a specialist model.

Image observations are not user utterances, verified world facts, privileged
instructions or canonical memory. All provider text is length-limited.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class IncomingVisualObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["analyzed", "unavailable"]
    source: Literal["vision_model", "core_unavailable"]
    description: str = Field(default="", max_length=1200)
    visible_text: list[str] = Field(default_factory=list, max_length=16)
    uncertainties: list[str] = Field(default_factory=list, max_length=8)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    perception_mode: Literal["detailed"] | None = None
    observation_version: Literal[2] | None = None
    provider: Literal["groq", "nvidia"] | None = None
    model: str | None = Field(default=None, max_length=128)
    failure_reason: Literal[
        "", "too_large", "download_failed", "unsupported_format",
        "processing_failed", "provider_failed", "unconfigured",
        "quota", "timeout", "refused", "invalid_observation", "provider_unavailable",
        "invalid_image", "focus_limit", "expired",
    ] = ""

    @field_validator("visible_text", "uncertainties")
    @classmethod
    def bounded_entries(cls, values: list[str]) -> list[str]:
        if any(not isinstance(item, str) or not item.strip() or len(item) > 240
               for item in values):
            raise ValueError("invalid visual evidence item")
        return values

    @model_validator(mode="after")
    def validate_profile(self):
        if self.perception_mode == "detailed":
            if (self.status != "analyzed" or self.observation_version != 2
                    or not self.provider or not self.model):
                raise ValueError("incomplete detailed observation provenance")
        elif (len(self.description) > 650 or len(self.visible_text) > 8
              or len(self.uncertainties) > 6
              or any(len(item) > 120 for item in self.visible_text + self.uncertainties)
              or any(value is not None for value in (self.observation_version, self.provider, self.model))):
            raise ValueError("legacy visual observation bounds exceeded")
        return self

    def as_context(self) -> dict[str, object]:
        data = self.model_dump(mode="json", exclude_none=True)
        # Keep successful visual-observation schema backward-compatible.
        if not data["failure_reason"]:
            data.pop("failure_reason")
        return data


def visual_context_for_request(value, *, remote: bool, private: bool, mode: str) -> dict | None:
    if not remote or not private or mode in {"reflection", "autonomous_initiation"}:
        return None
    try:
        observation = IncomingVisualObservation.model_validate(value)
    except (ValueError, TypeError):
        return None
    if observation.status == "analyzed":
        if (observation.source != "vision_model"
                or not observation.description.strip() or observation.failure_reason):
            return None
    elif observation.source != "core_unavailable" or observation.description or observation.visible_text:
        return None
    return observation.as_context()
