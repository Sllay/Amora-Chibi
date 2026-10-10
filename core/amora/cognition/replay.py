from __future__ import annotations

from pydantic import BaseModel, Field


class CognitiveReplayResult(BaseModel):
    provider: str
    model: str | None = None
    text: str = ""
    success: bool = False
    latency_ms: float | None = Field(default=None, ge=0.0)
    error_code: str | None = None
    finish_reason: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    reason_codes: list[str] = Field(default_factory=list)
