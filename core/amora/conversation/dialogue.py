from __future__ import annotations

from typing import Protocol


class DialogueProvider(Protocol):
    name: str

    async def reply(self, text: str, *, speaker_id: str | None, target_id: str | None) -> str: ...


class NoDialogueProvider:
    name = "none"

    async def reply(self, text: str, *, speaker_id: str | None, target_id: str | None) -> str:
        raise RuntimeError("no social dialogue provider configured")
