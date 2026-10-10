from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from amora.memory import MemoryKind


class MemoryProposal(BaseModel):
    kind: MemoryKind = MemoryKind.EPISODIC
    content: str = Field(min_length=1, max_length=2000)
    summary: str | None = Field(default=None, max_length=500)
    confidence: float = Field(default=0.6, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    novelty: float = Field(default=0.5, ge=0.0, le=1.0)
    emotional_weight: float = Field(default=0.0, ge=0.0, le=1.0)
    relationship_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    future_consequence: float = Field(default=0.0, ge=0.0, le=1.0)
    goal_relevance: float = Field(default=0.0, ge=0.0, le=1.0)
    entity_refs: dict[str, float] = Field(default_factory=dict)
    tags: set[str] = Field(default_factory=set)


class BeliefHypothesisProposal(BaseModel):
    statement: str = Field(min_length=1, max_length=2000)
    subject_id: str | None = None
    predicate: str = Field(min_length=1, max_length=160)
    object_value: Any = None
    confidence: float = Field(default=0.55, ge=0.0, le=0.89)
    context_key: str = Field(default="", max_length=240)


class ActionSuggestion(BaseModel):
    action_kind: str = Field(min_length=1, max_length=120)
    target_id: str | None = None
    urgency: float = Field(default=0.3, ge=0.0, le=1.0)
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    reason_code: str = Field(default="MODEL_SUGGESTION", min_length=1, max_length=160)


class CognitiveProposalBundle(BaseModel):
    memory_candidates: list[MemoryProposal] = Field(default_factory=list, max_length=6)
    belief_hypotheses: list[BeliefHypothesisProposal] = Field(default_factory=list, max_length=6)
    action_suggestions: list[ActionSuggestion] = Field(default_factory=list, max_length=6)
    tone: dict[str, float] = Field(default_factory=dict)
    provider: str | None = None
    model: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    accepted_memory_ids: list[str] = Field(default_factory=list)
    hypothesis_ids: list[str] = Field(default_factory=list)
    deferred_action_count: int = 0
