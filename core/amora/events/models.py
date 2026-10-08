from __future__ import annotations

from datetime import datetime, timezone
from enum import IntEnum, StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, field_validator


class EventPriority(IntEnum):
    BACKGROUND = 10
    LOW = 20
    NORMAL = 30
    HIGH = 40
    CRITICAL = 50


class PrivacyScope(StrEnum):
    PUBLIC = "PUBLIC"
    SOCIAL = "SOCIAL"
    PERSONAL = "PERSONAL"
    PRIVATE = "PRIVATE"
    SECRET = "SECRET"
    AUTH = "AUTH"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class AmoraEvent(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    schema_version: int = 1
    event_type: str
    timestamp: datetime = Field(default_factory=utc_now)
    source: str
    actor: str | None = None
    target: str | None = None
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    importance: float = Field(default=0.5, ge=0.0, le=1.0)
    priority: EventPriority = EventPriority.NORMAL
    payload: dict[str, Any] = Field(default_factory=dict)
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    session_id: str | None = None
    correlation_id: UUID | None = None
    causation_id: UUID | None = None
    causal_parent: UUID | None = None
    sequence: int | None = Field(default=None, ge=0)
    dedupe_key: str | None = None

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware")
        return value
