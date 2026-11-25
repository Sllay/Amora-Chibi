"""Bounded per-turn image evidence from a specialist model.

Image observations are not user utterances, verified world facts, privileged
instructions or canonical memory. All provider text is length-limited.
"""
from typing import Literal
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from .visual_spatial import SpatialObservation


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
    spatial: SpatialObservation | None = None
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
              or any(value is not None for value in (self.observation_version, self.provider, self.model, self.spatial))):
            raise ValueError("legacy visual observation bounds exceeded")
        return self

    def as_context(self) -> dict[str, object]:
        data = self.model_dump(mode="json", exclude_none=True)
        # Keep successful visual-observation schema backward-compatible.
        if not data["failure_reason"]:
            data.pop("failure_reason")
        return data


def _single_context(value) -> dict | None:
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


class PixelComparison(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: Literal["decoded_preview_pixels"]
    status: Literal["compared","different_dimensions"]
    first_size: tuple[int,int]
    second_size: tuple[int,int]
    sample_size: tuple[int,int] | None = None
    sampled: bool | None = None
    changed_pixels: int | None = Field(default=None, ge=0)
    changed_fraction: float | None = Field(default=None, ge=0, le=1)
    mean_channel_delta: float | None = Field(default=None, ge=0, le=1)

    @model_validator(mode="after")
    def check_metrics(self):
        if any(w<1 or h<1 or w*h>4194304 for w,h in (self.first_size,self.second_size)):
            raise ValueError("invalid decoded dimensions")
        values = (self.sample_size,self.sampled,self.changed_pixels,
                  self.changed_fraction,self.mean_channel_delta)
        if self.status=="different_dimensions":
            if self.first_size==self.second_size or any(x is not None for x in values):
                raise ValueError("invalid unequal dimensions")
            return self
        if self.first_size!=self.second_size or any(x is None for x in values):
            raise ValueError("incomplete pixel comparison")
        w,h=self.sample_size
        if not (1<=w<=min(512,self.first_size[0]) and 1<=h<=min(512,self.first_size[1])):
            raise ValueError("invalid sample size")
        if (self.sampled!=(self.sample_size!=self.first_size) or self.changed_pixels>w*h
                or abs(self.changed_fraction-self.changed_pixels/(w*h))>1e-9
                or self.mean_channel_delta>self.changed_fraction+1e-9
                or (self.changed_pixels==0)!=(self.mean_channel_delta==0)):
            raise ValueError("inconsistent pixel comparison")
        return self


class VisualBatchItem(BaseModel):
    model_config = ConfigDict(extra="forbid")
    image_number: int = Field(ge=1, le=16)
    observation: IncomingVisualObservation


class VisualBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    observation_version: Literal[3]
    source: Literal["vision_batch"]
    status: Literal["analyzed", "partial", "unavailable"]
    image_count: int = Field(ge=2, le=16)
    images: list[VisualBatchItem] = Field(min_length=1, max_length=3)
    first_two_pixels: PixelComparison | None = None

    @model_validator(mode="after")
    def validate_batch(self):
        numbers = [item.image_number for item in self.images]
        if numbers != list(range(1, len(numbers) + 1)) or len(numbers) > self.image_count:
            raise ValueError("invalid visual order")
        if any(_single_context(item.observation.as_context()) is None for item in self.images):
            raise ValueError("invalid image evidence")
        if self.first_two_pixels is not None and len(self.images)<2:
            raise ValueError("pixel pair requires two images")
        analyzed = sum(item.observation.status == "analyzed" for item in self.images)
        expected = "analyzed" if analyzed == self.image_count else "partial" if analyzed else "unavailable"
        if expected != self.status:
            raise ValueError("visual batch outcome mismatch")
        return self

    def as_context(self):
        return {"observation_version":3, "source":self.source, "status":self.status,
                "image_count":self.image_count,
                **({"first_two_pixels":self.first_two_pixels.model_dump(mode="json",exclude_none=True)} if self.first_two_pixels else {}),
                "images":[{"image_number":item.image_number,
                           "observation":item.observation.as_context()} for item in self.images]}


def _current_context(value):
    if isinstance(value, dict) and value.get("observation_version") == 3:
        try:
            return VisualBatch.model_validate(value).as_context()
        except (ValueError, TypeError):
            return None
    return _single_context(value)


class HistoricalVisualContext(BaseModel):
    """A prior observation, never a claim of newly inspected pixels."""
    model_config = ConfigDict(extra="forbid")
    observation_version: Literal[4]
    source: Literal["visual_reference"]
    status: Literal["analyzed","partial","unavailable"]
    reference_kind: Literal["reply","latest","unsupported"]
    observed_at: datetime | None = None
    historical: dict | None = None
    historical_disputed: Literal[True] | None = None
    review_requested: Literal[True] | None = None
    current: dict | None = None
    reinspection: dict | None = None
    historical_current_pixels: PixelComparison | None = None

    @model_validator(mode="after")
    def validate_reference(self):
        if self.historical is not None:
            self.historical = _current_context(self.historical)
            if self.historical is None or self.observed_at is None or self.observed_at.tzinfo is None:
                raise ValueError("invalid historical visual evidence")
        elif self.observed_at is not None:
            raise ValueError("timestamp without visual evidence")
        if self.historical_disputed and (self.historical is None or self.historical.get("status")!="unavailable"):
            raise ValueError("disputed visual evidence must not contain a prior assertion")
        if self.current is not None:
            self.current = _current_context(self.current)
            if self.current is None:
                raise ValueError("invalid current visual evidence")
        if self.reinspection is not None:
            self.reinspection = _single_context(self.reinspection)
            if self.reinspection is None or not self.review_requested or self.historical is None:
                raise ValueError("invalid fresh reinspection")
        if self.historical_current_pixels is not None and (self.historical is None or self.current is None):
            raise ValueError("pixel pair requires historical and current image")
        states = [v["status"] for v in (self.current,self.historical) if v is not None]
        expected = ("analyzed" if self.historical is not None and states and all(x=="analyzed" for x in states)
                    else "partial" if any(x in {"analyzed","partial"} for x in states)
                    else "unavailable")
        if self.status != expected or (self.reference_kind=="unsupported" and self.historical is not None):
            raise ValueError("visual reference outcome mismatch")
        return self


def visual_context_for_request(value, *, remote: bool, private: bool, mode: str) -> dict | None:
    if not remote or not private or mode in {"reflection", "autonomous_initiation", "scheduled_reminder"}:
        return None
    if isinstance(value,dict) and value.get("observation_version")==4:
        try:
            return HistoricalVisualContext.model_validate(value).model_dump(mode="json",exclude_none=True)
        except (ValueError, TypeError):
            return None
    return _current_context(value)
