from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from amora.events import PrivacyScope


class TurnState(StrEnum):
    LISTEN = "LISTEN"
    WAIT = "WAIT"
    BACKCHANNEL = "BACKCHANNEL"
    TAKE_TURN = "TAKE_TURN"
    INTERRUPT = "INTERRUPT"
    YIELD = "YIELD"


class UtteranceKind(StrEnum):
    SEMANTIC = "semantic"
    BACKCHANNEL = "backchannel"
    DIALOGUE = "dialogue"


class UtteranceStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    SPOKEN = "spoken"
    CANCELLED = "cancelled"


class TurnSignal(BaseModel):
    speaker_id: str | None = None
    addressee_id: str | None = None
    conversation_floor: str | None = None
    vad_active: bool = False
    pause_ms: int = Field(default=0, ge=0, le=60_000)
    partial_text: str | None = None
    direct_call: bool = False
    speech_requested: bool = False
    active_conversation: bool = False
    can_interrupt: bool = True
    urgency: float = Field(default=0.0, ge=0.0, le=1.0)
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    source_event_id: UUID | None = None
    dedupe_key: str | None = None


class TurnDecision(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    state: TurnState
    reason_codes: list[str] = Field(default_factory=list)
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    signal: TurnSignal
    created_at: datetime
    dedupe_key: str | None = None
    deduplicated: bool = False


class SpeechStyle(BaseModel):
    locale: str = "pt-BR"
    rate: float = Field(default=1.0, ge=0.5, le=2.0)
    pitch: float = Field(default=1.0, ge=0.5, le=2.0)
    volume: float = Field(default=1.0, ge=0.0, le=1.0)
    valence: float = Field(default=0.0, ge=-1.0, le=1.0)
    arousal: float = Field(default=0.0, ge=-1.0, le=1.0)


class SpeechUtterance(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1, max_length=800)
    target_id: str | None = None
    kind: UtteranceKind = UtteranceKind.SEMANTIC
    priority: int = Field(default=50, ge=0, le=100)
    style: SpeechStyle = Field(default_factory=SpeechStyle)
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    source_event_id: UUID | None = None
    device_id: str = "DEVICE_A15"
    status: UtteranceStatus = UtteranceStatus.QUEUED
    created_at: datetime
    claimed_at: datetime | None = None
    claim_expires_at: datetime | None = None
    spoken_at: datetime | None = None
    dedupe_key: str | None = None
    metadata: dict[str, object] = Field(default_factory=dict)


class SpeechAck(BaseModel):
    spoken: bool = True
    reason: str | None = None
