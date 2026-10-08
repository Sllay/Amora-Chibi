from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, model_validator

from amora.events import PrivacyScope


class EpistemicKind(StrEnum):
    OBSERVATION = "observation"
    REPORT = "report"
    BELIEF = "belief"
    INFERENCE = "inference"
    HYPOTHESIS = "hypothesis"


class EvidenceKind(StrEnum):
    EVENT = "event"
    MEMORY = "memory"
    CLAIM = "claim"
    EXTERNAL = "external"


class EvidenceRef(BaseModel):
    kind: EvidenceKind
    ref_id: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    note: str | None = Field(default=None, max_length=500)


class WorldClaimDraft(BaseModel):
    kind: EpistemicKind
    statement: str = Field(min_length=1, max_length=12000)
    subject_id: str | None = None
    predicate: str = Field(min_length=1, max_length=160)
    object_value: Any = None
    context_key: str = Field(default="", max_length=240)
    source: str = Field(min_length=1, max_length=240)
    source_entity_id: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    remote_processing_allowed: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_temporal_window(self) -> "WorldClaimDraft":
        if self.valid_from and self.valid_until and self.valid_until < self.valid_from:
            raise ValueError("valid_until cannot precede valid_from")
        return self


class WorldClaim(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    kind: EpistemicKind
    statement: str
    subject_id: str | None = None
    predicate: str
    object_value: Any = None
    context_key: str = ""
    source: str
    source_entity_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    evidence: list[EvidenceRef] = Field(default_factory=list)
    valid_from: datetime
    valid_until: datetime | None = None
    created_at: datetime
    updated_at: datetime
    canonical: bool = False
    supersedes_claim_id: UUID | None = None
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    remote_processing_allowed: bool = True
    metadata: dict[str, Any] = Field(default_factory=dict)


class WorldWriteResult(BaseModel):
    claim: WorldClaim
    reason_codes: list[str] = Field(default_factory=list)
    revised_claim_id: UUID | None = None


class WorldQuery(BaseModel):
    subject_id: str | None = None
    predicate: str | None = None
    context_key: str | None = None
    kinds: set[EpistemicKind] = Field(default_factory=set)
    include_expired: bool = False
    canonical_only: bool = False
    limit: int = Field(default=50, ge=1, le=500)


class SelfLayer(StrEnum):
    CORE_IDENTITY = "core_identity"
    TRAITS = "traits"
    PREFERENCES = "preferences"
    RELATIONSHIPS = "relationships"
    SELF_NARRATIVE = "self_narrative"
    CURRENT_STATE = "current_state"


class SelfEntry(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    layer: SelfLayer
    key: str = Field(min_length=1, max_length=160)
    value: Any
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = Field(min_length=1, max_length=240)
    locked: bool = False
    created_at: datetime
    updated_at: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class RelationshipState(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    source_id: str
    relation_type: str = Field(min_length=1, max_length=160)
    target_id: str
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source_of_truth: str = Field(min_length=1, max_length=240)
    valid_from: datetime
    valid_until: datetime | None = None
    created_at: datetime
    supersedes_id: UUID | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class PerspectiveConsolidationStatus(StrEnum):
    CONSOLIDATED = "consolidated"
    DEFERRED = "deferred"


class PerspectiveConsolidationResult(BaseModel):
    status: PerspectiveConsolidationStatus
    claim: WorldClaim | None = None
    reason_codes: list[str] = Field(default_factory=list)
