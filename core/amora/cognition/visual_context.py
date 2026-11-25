"""Bounded per-turn image evidence from a specialist model.

Image observations are not user utterances, verified world facts, privileged
instructions or canonical memory. All provider text is length-limited.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class IncomingVisualObservation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: Literal["analyzed", "unavailable"]
    source: Literal["vision_model", "core_unavailable"]
    description: str = Field(default="", max_length=650)
    visible_text: list[str] = Field(default_factory=list, max_length=8)
    uncertainties: list[str] = Field(default_factory=list, max_length=6)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    failure_reason: Literal[
        "", "too_large", "download_failed", "unsupported_format",
        "processing_failed", "provider_failed", "unconfigured",
        "quota", "timeout", "refused", "invalid_observation", "provider_unavailable",
        "invalid_image", "focus_limit", "expired",
    ] = ""

    @field_validator("visible_text", "uncertainties")
    @classmethod
    def bounded_entries(cls, values: list[str]) -> list[str]:
        if any(not isinstance(item, str) or not item.strip() or len(item) > 120
               for item in values):
            raise ValueError("invalid visual evidence item")
        return values

    def as_context(self) -> dict[str, object]:
        data = self.model_dump(mode="json")
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
