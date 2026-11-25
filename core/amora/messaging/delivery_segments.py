"""Versioned Discord text delivery, independent of speech segmentation."""
from __future__ import annotations
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

CHECKPOINT = "discord_delivery_v1"


def split_delivery_text(text: str) -> list[str]:
    """Stable v1 protocol: respect UTF-16 budget and Unicode scalars."""
    remaining = text.strip()
    chunks = []
    while remaining:
        units = 0
        end = 0
        for char in remaining:
            size = 2 if ord(char) > 0xFFFF else 1
            if units + size > 2000:
                break
            units += size
            end += 1
        if end == len(remaining):
            chunks.append(remaining)
            break
        cut = remaining.rfind("\n", 0, end+1)
        if cut < end*0.6:
            cut = remaining.rfind(" ", 0, end+1)
        if cut < end*0.5:
            cut = end
        chunks.append(remaining[:cut].rstrip())
        remaining = remaining[cut:].lstrip()
    return chunks


class DeliveryCheckpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal[1] = 1
    count: int = Field(ge=2, le=32)
    receipts: list[str] = Field(default_factory=list,max_length=32)
    pending_lease: str | None = Field(default=None,max_length=64)

    @model_validator(mode="after")
    def check_receipts(self):
        if (len(self.receipts)>self.count or len(set(self.receipts))!=len(self.receipts)
                or (self.pending_lease is not None and len(self.receipts)==self.count)
                or any(not x.isascii() or not x.isdecimal() or not 17<=len(x)<=20 for x in self.receipts)):
            raise ValueError("invalid Discord segment receipts")
        return self


class DeliverySegmentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    phase: Literal["prepare","confirm"]
    index: int = Field(ge=0,lt=32)
    lease: str = Field(min_length=1,max_length=64)
