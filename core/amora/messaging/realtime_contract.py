"""Internal Realtime delivery contract. No authorization is granted by this type.

CandidateEvent is not this snapshot. Only a future authorized controller may
construct and dispatch snapshots after applicable validation. Cursor is a
presentation attribute, not semantic text or canonical memory.
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, model_validator

CURSOR = "\u258d"
DISCORD_LIMIT = 2000


def utf16_units(text: str) -> int:
    return len(text.encode("utf-16-le", errors="strict")) // 2


class DeliverySnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)
    version: Literal[1] = 1
    turn_id: UUID
    group_id: UUID
    attempt_id: UUID
    revision: int = Field(ge=1)
    seq: int = Field(ge=1)
    status: Literal["writing", "final", "interrupted"]
    text: str = Field(repr=False, max_length=6000)

    @model_validator(mode="after")
    def check_text(self):
        utf16_units(self.text)
        if not self.text.strip():
            raise ValueError("EMPTY_SNAPSHOT")
        return self

    def discord_block(self) -> str:
        """Render one bounded block. Splitting belongs to the controller.

        Never silently truncate. Interrupted text is retained without cursor,
        with an explicit interruption marker rather than a false completion.
        """
        tail = " " + CURSOR if self.status == "writing" else (
            "\n\n[Resposta interrompida.]" if self.status == "interrupted" else "")
        visible = self.text + tail
        if utf16_units(visible) > DISCORD_LIMIT:
            raise ValueError("DISCORD_BLOCK_LIMIT")
        return visible
