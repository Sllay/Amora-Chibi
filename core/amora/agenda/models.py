"""Strict user-owned agenda contract. All timestamps are aware, stored UTC."""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from pydantic import BaseModel, ConfigDict, Field, model_validator

OWNER = "PERSON_BK"
ZONE = "America/Recife"
MAX_HORIZON_DAYS = 365 * 30
MIN_INTERVAL_MINUTES = 10


def utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("AGENDA_TIMEZONE_REQUIRED")
    return value.astimezone(timezone.utc)


class AgendaCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID = Field(default_factory=uuid4)
    text: str = Field(min_length=1, max_length=500)
    due_at: datetime
    timezone: str = ZONE
    interval_minutes: int | None = Field(default=None, ge=MIN_INTERVAL_MINUTES, le=10080)
    until_at: datetime | None = None

    @model_validator(mode="after")
    def validate_agenda(self):
        if not self.text.strip():
            raise ValueError("AGENDA_TEXT_REQUIRED")
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("AGENDA_TIMEZONE_INVALID") from None
        due = utc(self.due_at)
        if self.until_at is not None:
            end = utc(self.until_at)
            if self.interval_minutes is None or end <= due:
                raise ValueError("AGENDA_UNTIL_REQUIRES_INTERVAL")
            if end - due > timedelta(days=MAX_HORIZON_DAYS):
                raise ValueError("AGENDA_INTERVAL_HORIZON")
        if self.interval_minutes is not None and self.until_at is None:
            raise ValueError("AGENDA_INTERVAL_END_REQUIRED")
        self.text = self.text.strip()
        return self


class AgendaItem(AgendaCreate):
    id: UUID
    created_at: datetime
    account_scope: str = Field(min_length=1, max_length=96)
    conversation_id: str = Field(min_length=1, max_length=512)
    external_actor_id: str = Field(min_length=15, max_length=22)
    status: Literal["active", "cancelled"] = "active"

    @model_validator(mode="after")
    def require_channel(self):
        if not self.external_actor_id.isdecimal():
            raise ValueError("AGENDA_TARGET_NOT_VERIFIED")
        return self


def event_payload(item: AgendaItem) -> dict:
    return item.model_dump(mode="json", exclude={"status"})


def occurrence(item: AgendaItem, now: datetime):
    """Current due occurrence, if within the five-minute delivery window."""
    now, due = utc(now), utc(item.due_at)
    if now < due:
        return None
    if item.interval_minutes is None:
        target = due
    else:
        index = int((now - due).total_seconds()) // (item.interval_minutes * 60)
        target = due + timedelta(minutes=index * item.interval_minutes)
        if item.until_at is None or target > utc(item.until_at):
            return None
    return target if now - target < timedelta(minutes=5) else None


def next_occurrence(item: AgendaItem, now: datetime):
    now, due = utc(now), utc(item.due_at)
    if now <= due:
        return due
    if item.interval_minutes is None:
        return None
    seconds = item.interval_minutes * 60
    elapsed = (now - due).total_seconds()
    n = int(elapsed // seconds) + (0 if elapsed % seconds == 0 else 1)
    target = due + timedelta(seconds=n * seconds)
    return target if target <= utc(item.until_at) else None
