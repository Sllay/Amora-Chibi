from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

from pydantic import BaseModel, Field

from amora.identity import EntityRegistry
from .scene import CurrentSocialScene


class AddresseeKind(StrEnum):
    AMORA = "AMORA"
    PERSON = "PERSON"
    CHAT = "CHAT"
    GROUP = "GROUP"
    SELF_TALK = "SELF_TALK"
    UNKNOWN = "UNKNOWN"


class AddresseeResult(BaseModel):
    kind: AddresseeKind
    entity_id: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    candidates: list[str] = Field(default_factory=list)
    reason_codes: list[str] = Field(default_factory=list)


class AddresseeEngine:
    """Conservative addressee inference for text that already has a speaker candidate.

    V0.2 intentionally prefers UNKNOWN over making Amora answer every utterance
    captured by the microphone. Audio/prosody/gaze hints can be added later.
    """

    def __init__(self, registry: EntityRegistry, *, amora_id: str = "ENTITY_AMORA") -> None:
        self.registry = registry
        self.amora_id = amora_id

    def infer(
        self,
        text: str,
        *,
        speaker_id: str | None,
        scene: CurrentSocialScene | None = None,
    ) -> AddresseeResult:
        normalized = self._normalize_text(text)

        leading = re.match(r"^\s*([^,:;!?]{1,40})\s*[,!:]", text)
        if leading:
            resolved = self.registry.resolve_alias(leading.group(1).strip())
            if resolved and resolved.id != speaker_id:
                return self._entity_result(resolved.id, 0.99, ["EXPLICIT_LEADING_VOCATIVE"])

        explicit_ei = re.match(r"^\s*ei[ ,]+([^,:;!?]{1,40})(?:[,!:]|\b)", normalized)
        if explicit_ei:
            resolved = self.registry.resolve_alias(explicit_ei.group(1).strip())
            if resolved and resolved.id != speaker_id:
                return self._entity_result(resolved.id, 0.98, ["EXPLICIT_EI_VOCATIVE"])

        # A standalone mention of Amora is useful, but weaker than a leading
        # vocative. This distinction handles e.g. "João, pergunta pra Amora...".
        if re.search(r"(?<!\w)amora(?!\w)", normalized):
            return self._entity_result(self.amora_id, 0.90, ["AMORA_NAME_MENTION"])

        if scene and scene.active_addressee and scene.current_speaker == speaker_id:
            return self._entity_result(
                scene.active_addressee,
                0.78,
                ["CONVERSATION_FLOOR_CONTINUITY"],
            )

        return AddresseeResult(
            kind=AddresseeKind.UNKNOWN,
            confidence=0.25,
            reason_codes=["NO_RELIABLE_ADDRESSEE_SIGNAL"],
        )

    def _entity_result(
        self,
        entity_id: str,
        confidence: float,
        reason_codes: list[str],
    ) -> AddresseeResult:
        kind = AddresseeKind.AMORA if entity_id == self.amora_id else AddresseeKind.PERSON
        return AddresseeResult(
            kind=kind,
            entity_id=entity_id,
            confidence=confidence,
            candidates=[entity_id],
            reason_codes=reason_codes,
        )

    @staticmethod
    def _normalize_text(value: str) -> str:
        value = unicodedata.normalize("NFKC", value).casefold()
        return re.sub(r"\s+", " ", value).strip()
