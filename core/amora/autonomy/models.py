from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class DriveType(StrEnum):
    SOCIAL = "social"
    CURIOSITY = "curiosity"
    PLAY = "play"
    NOVELTY = "novelty"
    EXPRESSION = "expression"
    COMPETENCE = "competence"
    REST = "rest"


class DesireKind(StrEnum):
    SOCIAL_BID = "social_bid"
    INITIATE_CONTACT = "initiate_contact"
    OBSERVE = "observe"
    ASK_QUESTION = "ask_question"
    PLAY = "play"
    SEEK_NOVELTY = "seek_novelty"
    EXPRESS = "express"
    PRACTICE = "practice"
    REST = "rest"
    SEEK_REPAIR = "seek_repair"
    EXPRESS_GRATITUDE = "express_gratitude"
    SET_BOUNDARY = "set_boundary"
    WITHDRAW = "withdraw"
    WAIT = "wait"


class IntentionStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class DecisionOutcome(StrEnum):
    SELECTED = "selected"
    KEPT_CURRENT = "kept_current"
    DEFERRED = "deferred"
    SILENCE = "silence"


class DriveState(BaseModel):
    drive: DriveType
    need: float = Field(ge=0.0, le=1.0)
    baseline: float = Field(ge=0.0, le=1.0)
    growth_per_hour: float = Field(ge=0.0, le=1.0)
    updated_at: datetime


class DriveAdjustment(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    drive: DriveType
    delta: float = Field(ge=-1.0, le=1.0)
    reason: str = Field(min_length=1, max_length=300)
    source_event_id: UUID | None = None
    created_at: datetime
    dedupe_key: str | None = Field(default=None, max_length=256)


class DecisionContext(BaseModel):
    is_idle: bool = True
    can_interrupt: bool = True
    active_conversation: bool = False
    active_addressee: str | None = None
    available_entities: set[str] = Field(default_factory=set)
    busy_entities: set[str] = Field(default_factory=set)
    opportunity: float = Field(default=1.0, ge=0.0, le=1.0)
    interruption_penalty: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)


class BeliefSnapshot(BaseModel):
    world_claim_ids: list[UUID] = Field(default_factory=list)
    mood_valence: float = Field(ge=-1.0, le=1.0)
    mood_arousal: float = Field(ge=0.0, le=1.0)
    mood_dominance: float = Field(ge=-1.0, le=1.0)
    context: DecisionContext
    observed_at: datetime


class UtilityFactors(BaseModel):
    drive_need: float = Field(ge=0.0, le=1.0)
    opportunity: float = Field(ge=0.0, le=1.0)
    personality: float = Field(ge=0.0, le=1.0)
    emotion: float = Field(ge=0.0, le=1.0)
    relationship: float = Field(ge=0.0, le=1.0)
    expected_reward: float = Field(ge=0.0, le=1.0)
    cost: float = Field(ge=0.0, le=1.0)
    interruption_penalty: float = Field(ge=0.0, le=1.0)


class DesireCandidate(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    kind: DesireKind
    source_kind: str
    source_ref_id: str | None = None
    target_id: str | None = None
    primary_drive: DriveType
    factors: UtilityFactors
    utility: float = Field(ge=-1.0, le=1.0)
    urgency: float = Field(default=0.0, ge=0.0, le=1.0)
    created_at: datetime
    expires_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class Intention(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    kind: DesireKind
    target_id: str | None = None
    source_kind: str
    source_ref_id: str | None = None
    selected_utility: float = Field(ge=-1.0, le=1.0)
    urgency: float = Field(ge=0.0, le=1.0)
    commitment_strength: float = Field(default=0.65, ge=0.0, le=1.0)
    created_at: datetime
    updated_at: datetime
    expires_at: datetime | None = None
    status: IntentionStatus = IntentionStatus.ACTIVE
    metadata: dict[str, Any] = Field(default_factory=dict)


class DecisionRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    outcome: DecisionOutcome
    selected_intention_id: UUID | None = None
    previous_intention_id: UUID | None = None
    selected_utility: float | None = Field(default=None, ge=-1.0, le=1.0)
    silence_utility: float = Field(ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)
    created_at: datetime
    dedupe_key: str | None = Field(default=None, max_length=256)


class AutonomyCycleResult(BaseModel):
    beliefs: BeliefSnapshot
    drives: list[DriveState]
    candidates: list[DesireCandidate]
    decision: DecisionRecord
    intention: Intention | None = None
    deduplicated: bool = False
