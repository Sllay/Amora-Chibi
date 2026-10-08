from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field


class AudioSegment(BaseModel):
    path: Path
    mime_type: str = "audio/wav"
    language: str | None = "pt"
    duration_seconds: float | None = Field(default=None, ge=0.0)


class Transcript(BaseModel):
    text: str
    provider: str
    model: str
    language: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    request_id: str | None = None
    provider_metadata: dict[str, str] = Field(default_factory=dict)


class VoiceActivityResult(BaseModel):
    speech: bool
    score: float = Field(ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)


class SpeakerResult(BaseModel):
    speaker_id: str
    confidence: float = Field(ge=0.0, le=1.0)
    known_person_id: str | None = None
    diarization_label: str | None = None
    reason_codes: list[str] = Field(default_factory=list)
