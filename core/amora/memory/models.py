from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator, model_validator

from amora.events import PrivacyScope


class MemoryKind(StrEnum):
    EPISODIC = "episodic"
    SEMANTIC = "semantic"
    RELATIONAL = "relational"


class ProvenanceKind(StrEnum):
    OBSERVATION = "observation"
    REPORT = "report"
    USER_ASSERTION = "user_assertion"
    INFERENCE = "inference"
    DERIVED = "derived"


class MemoryProvenance(BaseModel):
    kind: ProvenanceKind
    source: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_event_id: UUID | None = None
    actor_id: str | None = None
    session_id: str | None = None
    derived_from: list[UUID] = Field(default_factory=list)


class MemoryCandidate(BaseModel):
    kind: MemoryKind
    content: str = Field(min_length=1, max_length=12000)
    summary: str | None = Field(default=None, max_length=1000)
    provenance: MemoryProvenance
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    owner_entity_id: str | None = None
    audience: set[str] = Field(default_factory=set)
    remote_processing_allowed: bool = True
    entity_refs: dict[str, float] = Field(default_factory=dict)
    tags: set[str] = Field(default_factory=set)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    novelty: float = Field(default=0.5, ge=0.0, le=1.0)
    emotional_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    relationship_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    future_consequence: float = Field(default=0.0, ge=0.0, le=1.0)
    rarity: float = Field(default=0.0, ge=0.0, le=1.0)
    goal_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    repetition: float = Field(default=0.0, ge=0.0, le=1.0)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @field_validator("entity_refs")
    @classmethod
    def validate_entity_confidence(cls, value: dict[str, float]) -> dict[str, float]:
        for entity_id, confidence in value.items():
            if not entity_id.strip():
                raise ValueError("entity id cannot be blank")
            if not 0.0 <= confidence <= 1.0:
                raise ValueError("entity confidence must be between 0 and 1")
        return value

    @model_validator(mode="after")
    def validate_temporal_window(self) -> "MemoryCandidate":
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until cannot precede valid_from")
        return self


class MemoryRecord(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    kind: MemoryKind
    content: str
    summary: str | None = None
    provenance: MemoryProvenance
    privacy_scope: PrivacyScope
    owner_entity_id: str | None = None
    audience: set[str] = Field(default_factory=set)
    remote_processing_allowed: bool = True
    entity_refs: dict[str, float] = Field(default_factory=dict)
    tags: set[str] = Field(default_factory=set)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    created_at: datetime
    updated_at: datetime
    importance: float = Field(ge=0.0, le=1.0)
    gate_score: float = Field(ge=0.0, le=1.0)
    retrievability: float = Field(default=1.0, ge=0.0, le=1.0)
    access_count: int = Field(default=0, ge=0)
    last_accessed_at: datetime | None = None
    archived_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class MemoryWriteDecision(BaseModel):
    store: bool
    score: float = Field(ge=0.0, le=1.0)
    threshold: float = Field(ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)
    recommended_kind: MemoryKind | None = None


class MemoryQuery(BaseModel):
    text: str = ""
    kinds: set[MemoryKind] = Field(default_factory=set)
    entity_ids: set[str] = Field(default_factory=set)
    after: datetime | None = None
    before: datetime | None = None
    limit: int = Field(default=8, ge=1, le=50)
    mmr_lambda: float = Field(default=0.75, ge=0.0, le=1.0)
    include_archived: bool = False

    @model_validator(mode="after")
    def validate_window(self) -> "MemoryQuery":
        if self.after and self.before and self.before < self.after:
            raise ValueError("before cannot precede after")
        return self


class MemoryHit(BaseModel):
    memory: MemoryRecord
    score: float
    reason_codes: list[str] = Field(default_factory=list)
