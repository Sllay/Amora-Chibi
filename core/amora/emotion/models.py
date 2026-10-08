from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from amora.events import PrivacyScope


class EmotionType(StrEnum):
    JOY = "joy"
    GRATITUDE = "gratitude"
    RELIEF = "relief"
    ANGER = "anger"
    IRRITATION = "irritation"
    FEAR = "fear"
    DISAPPOINTMENT = "disappointment"


class SocialBidType(StrEnum):
    EXPRESS_GRATITUDE = "express_gratitude"
    SEEK_CONTACT = "seek_contact"
    SEEK_REPAIR = "seek_repair"
    SET_BOUNDARY = "set_boundary"
    WITHDRAW = "withdraw"


class SocialBidStatus(StrEnum):
    PENDING = "pending"
    CONSUMED = "consumed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class AppraisalInput(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    outcome_valence: float = Field(ge=-1.0, le=1.0)
    target_id: str | None = None
    agent_id: str | None = None
    cause_event_id: UUID | None = None
    goal_relevance: float = Field(default=0.5, ge=0.0, le=1.0)
    novelty: float = Field(default=0.5, ge=0.0, le=1.0)
    expectedness: float = Field(default=0.5, ge=0.0, le=1.0)
    agency: float = Field(default=0.5, ge=0.0, le=1.0)
    control: float = Field(default=0.5, ge=0.0, le=1.0)
    intentionality: float = Field(default=0.5, ge=0.0, le=1.0)
    norm_violation: float = Field(default=0.0, ge=0.0, le=1.0)
    relationship_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    dedupe_key: str | None = Field(default=None, max_length=256)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def default_target_from_agent(self) -> "AppraisalInput":
        if self.target_id is None and self.agent_id is not None:
            self.target_id = self.agent_id
        return self


class AppraisalResult(BaseModel):
    emotion: EmotionType | None
    intensity: float = Field(ge=0.0, le=1.0)
    blame: float = Field(ge=0.0, le=1.0)
    spillover: float = Field(ge=0.0, le=1.0)
    mood_valence_delta: float = Field(ge=-1.0, le=1.0)
    mood_arousal_delta: float = Field(ge=-1.0, le=1.0)
    mood_dominance_delta: float = Field(ge=-1.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)


class EmotionEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    emotion: EmotionType
    intensity: float = Field(ge=0.0, le=1.0)
    cause_event_id: UUID | None = None
    cause_description: str
    target_id: str | None = None
    agent_id: str | None = None
    blame: float = Field(ge=0.0, le=1.0)
    intentionality: float = Field(ge=0.0, le=1.0)
    spillover: float = Field(ge=0.0, le=1.0)
    appraisal: AppraisalInput
    privacy_scope: PrivacyScope
    created_at: datetime
    dedupe_key: str | None = None


class MoodState(BaseModel):
    valence: float = Field(default=0.0, ge=-1.0, le=1.0)
    arousal: float = Field(default=0.0, ge=0.0, le=1.0)
    dominance: float = Field(default=0.0, ge=-1.0, le=1.0)
    updated_at: datetime


class EntityAffect(BaseModel):
    entity_id: str
    anger: float = Field(default=0.0, ge=0.0, le=1.0)
    affection: float = Field(default=0.0, ge=0.0, le=1.0)
    trust: float = Field(default=0.5, ge=0.0, le=1.0)
    irritation: float = Field(default=0.0, ge=0.0, le=1.0)
    fear: float = Field(default=0.0, ge=0.0, le=1.0)
    gratitude: float = Field(default=0.0, ge=0.0, le=1.0)
    updated_at: datetime


class SocialBid(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    bid_type: SocialBidType
    target_id: str | None = None
    strength: float = Field(ge=0.0, le=1.0)
    reason: str
    source_emotion_event_id: UUID
    created_at: datetime
    expires_at: datetime
    status: SocialBidStatus = SocialBidStatus.PENDING
    metadata: dict[str, Any] = Field(default_factory=dict)


class EmotionProcessResult(BaseModel):
    appraisal: AppraisalResult
    event: EmotionEvent | None = None
    mood: MoodState
    target_affect: EntityAffect | None = None
    social_bids: list[SocialBid] = Field(default_factory=list)
    deduplicated: bool = False
