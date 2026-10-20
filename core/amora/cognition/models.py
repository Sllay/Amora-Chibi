from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from amora.events import PrivacyScope


class MessageSource(StrEnum):
    STT = "stt"
    TEXT = "text"
    COGNITIVE = "cognitive"
    LOCAL_FALLBACK = "local_fallback"
    TTS = "tts"
    AUTONOMY = "autonomy"


class MessageStatus(StrEnum):
    RECEIVED = "received"
    DRAFT = "draft"
    QUEUED = "queued"
    SPEAKING = "speaking"
    SPOKEN = "spoken"
    INTERRUPTED = "interrupted"
    FAILED = "failed"


class JournalStage(StrEnum):
    INPUT_OBSERVED = "input_observed"
    TURN_DECIDED = "turn_decided"
    CONTEXT_COMPILED = "context_compiled"
    PRIVACY_FILTERED = "privacy_filtered"
    PROVIDER_SELECTED = "provider_selected"
    MODEL_STARTED = "model_started"
    MODEL_DELTA = "model_delta"
    RESPONSE_PLANNED = "response_planned"
    REFLECTION_STARTED = "reflection_started"
    PROPOSALS_GENERATED = "proposals_generated"
    PROPOSALS_GATED = "proposals_gated"
    SPEECH_SEGMENT_READY = "speech_segment_ready"
    TTS_STARTED = "tts_started"
    TTS_FINISHED = "tts_finished"
    BARGE_IN = "barge_in"
    TURN_FINISHED = "turn_finished"
    ERROR = "error"


class ConversationMessage(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    turn_id: UUID = Field(default_factory=uuid4)
    speaker_id: str | None = None
    addressee_id: str | None = None
    source: MessageSource
    raw_text: str | None = None
    normalized_text: str | None = None
    spoken_text: str | None = None
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    remote_processing_allowed: bool = False
    stt_confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    provider: str | None = None
    model: str | None = None
    status: MessageStatus = MessageStatus.RECEIVED
    interrupted: bool = False
    source_event_id: UUID | None = None
    session_id: str | None = None
    started_at: datetime
    finished_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str | None = None


class CognitiveJournalEntry(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    turn_id: UUID
    stage: JournalStage
    timestamp: datetime
    source: str = "cognition"
    summary: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
    payload: dict[str, Any] = Field(default_factory=dict)
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    provider: str | None = None
    model: str | None = None
    duration_ms: float | None = Field(default=None, ge=0.0)
    correlation_id: UUID | None = None
    error_code: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
    dedupe_key: str | None = None


class SpeechSegment(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    turn_id: UUID
    ordinal: int = Field(ge=0)
    display_text: str
    speech_text: str
    status: MessageStatus = MessageStatus.QUEUED
    tts_provider: str | None = None
    voice_id: str | None = None
    queued_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    interrupted: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ContextMemory(BaseModel):
    id: str
    kind: str
    content: str
    score: float
    confidence: float
    privacy_scope: str
    reason_codes: list[str] = Field(default_factory=list)


class CognitiveContext(BaseModel):
    turn_id: UUID
    created_at: datetime
    speaker_id: str
    target_id: str | None
    user_text: str
    self_model: dict[str, dict[str, Any]] = Field(default_factory=dict)
    relationships: list[dict[str, Any]] = Field(default_factory=list)
    mood: dict[str, float] = Field(default_factory=dict)
    directed_affect: dict[str, float] = Field(default_factory=dict)
    drives: dict[str, float] = Field(default_factory=dict)
    active_intention: dict[str, Any] | None = None
    memories: list[ContextMemory] = Field(default_factory=list)
    world_claims: list[dict[str, Any]] = Field(default_factory=list)
    recent_messages: list[dict[str, Any]] = Field(default_factory=list)
    dialogue_plan: dict[str, Any] = Field(default_factory=dict)
    # Ephemeral aid from a specialist reasoning model. It is never canonical
    # identity/state and is not persisted as a fact merely because a model wrote it.
    specialist_support: dict[str, Any] | None = None
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    remote_processing_allowed: bool = False
    token_budget: int = Field(default=3200, ge=256, le=32000)
    reason_codes: list[str] = Field(default_factory=list)


class CognitiveRequest(BaseModel):
    text: str = Field(min_length=1, max_length=12000)
    speaker_id: str
    target_id: str | None = "ENTITY_AMORA"
    source_event_id: UUID | None = None
    session_id: str | None = None
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    remote_processing_allowed: bool = False
    turn_id: UUID = Field(default_factory=uuid4)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CognitiveResponsePlan(BaseModel):
    turn_id: UUID
    display_text: str
    speech_text: str
    speech_segments: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    fallback_used: bool = False
    reason_codes: list[str] = Field(default_factory=list)
    input_tokens: int | None = None
    output_tokens: int | None = None
    latency_ms: float | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)
