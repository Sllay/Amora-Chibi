from __future__ import annotations

import re
import unicodedata
from enum import StrEnum

from pydantic import BaseModel, Field

from amora.identity import EntityRegistry


class ResolutionStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class PerspectiveFrame(BaseModel):
    speaker_id: str
    addressee_id: str | None = None
    focus_entity_id: str | None = None


class PerspectiveStack:
    """Stack of deictic frames used for direct and quoted speech."""

    def __init__(self, frame: PerspectiveFrame) -> None:
        self._frames = [frame]

    @property
    def current(self) -> PerspectiveFrame:
        return self._frames[-1]

    @property
    def depth(self) -> int:
        return len(self._frames)

    def push(self, frame: PerspectiveFrame) -> None:
        self._frames.append(frame)

    def pop(self) -> PerspectiveFrame:
        if len(self._frames) == 1:
            raise RuntimeError("cannot pop root perspective frame")
        return self._frames.pop()


class ReferenceResolution(BaseModel):
    expression: str
    status: ResolutionStatus
    entity_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    reason_codes: list[str] = Field(default_factory=list)

    @property
    def entity_id(self) -> str | None:
        if self.status == ResolutionStatus.RESOLVED and len(self.entity_ids) == 1:
            return self.entity_ids[0]
        return None


class PerspectiveResolver:
    _first_person = {"eu", "me", "mim", "comigo"}
    _second_person = {"você", "voce", "te", "ti", "contigo"}
    _third_person = {"ele", "ela", "dele", "dela"}
    _kinship = {
        "mãe": "mother_of",
        "mae": "mother_of",
        "pai": "father_of",
        "filha": "child_of",
        "filho": "child_of",
    }
    _possessive_owner = {
        "minha": "speaker",
        "meu": "speaker",
        "minhas": "speaker",
        "meus": "speaker",
        "sua": "addressee",
        "seu": "addressee",
        "suas": "addressee",
        "seus": "addressee",
        "tua": "addressee",
        "teu": "addressee",
        "tuas": "addressee",
        "teus": "addressee",
    }

    def __init__(self, registry: EntityRegistry) -> None:
        self.registry = registry

    def resolve(
        self,
        expression: str,
        stack: PerspectiveStack,
        *,
        candidates: list[str] | None = None,
    ) -> ReferenceResolution:
        expr = self._normalize(expression)
        frame = stack.current
        candidates = candidates or []

        direct = self.registry.get(expression) or self.registry.resolve_alias(expression)
        if direct:
            return self._resolved(expression, direct.id, 0.99, ["CANONICAL_ENTITY_OR_ALIAS"])

        if expr in self._first_person:
            return self._resolved(expression, frame.speaker_id, 0.99, ["FIRST_PERSON_TO_SPEAKER"])

        if expr in self._second_person:
            if frame.addressee_id:
                return self._resolved(expression, frame.addressee_id, 0.98, ["SECOND_PERSON_TO_ADDRESSEE"])
            return self._unresolved(expression, ["ADDRESSEE_UNKNOWN"])

        possessive = re.fullmatch(
            r"(minha|meu|minhas|meus|sua|seu|suas|seus|tua|teu|tuas|teus)\s+"
            r"(mãe|mae|pai|filha|filho)",
            expr,
        )
        if possessive:
            owner_source = self._possessive_owner[possessive.group(1)]
            possessor = frame.speaker_id if owner_source == "speaker" else frame.addressee_id
            if not possessor:
                return self._unresolved(expression, ["POSSESSOR_UNKNOWN"])
            base_confidence = 0.96 if owner_source == "speaker" else 0.92
            return self._resolve_relation(
                expression,
                possessor,
                possessive.group(2),
                base_confidence,
                [f"POSSESSOR_{owner_source.upper()}"],
            )

        third_person_relation = re.fullmatch(
            r"(mãe|mae|pai|filha|filho)\s+(dele|dela)",
            expr,
        )
        if third_person_relation:
            owner = self._resolve_third_person_owner(
                third_person_relation.group(2),
                frame,
                candidates,
            )
            if owner.status != ResolutionStatus.RESOLVED:
                return ReferenceResolution(
                    expression=expression,
                    status=owner.status,
                    entity_ids=owner.entity_ids,
                    confidence=owner.confidence,
                    reason_codes=owner.reason_codes + ["RELATIONAL_OWNER_UNRESOLVED"],
                )
            return self._resolve_relation(
                expression,
                owner.entity_id,
                third_person_relation.group(1),
                min(0.84, owner.confidence),
                owner.reason_codes,
            )

        # A bare relational noun is not a global alias. For V0.2 we only
        # accept the safe vocative case where the speaker has exactly the
        # relevant parent relation to the current addressee.
        if expr in {"filha", "filho"} and frame.addressee_id:
            children = self._relation_entities(frame.speaker_id, "child_of")
            if frame.addressee_id in children:
                return self._resolved(
                    expression,
                    frame.addressee_id,
                    0.93,
                    ["RELATIONAL_VOCATIVE_MATCHES_ADDRESSEE"],
                )

        if expr in self._third_person:
            return self._resolve_third_person_owner(expr, frame, candidates)

        return self._unresolved(expression, ["NO_RESOLUTION_RULE"])

    def _resolve_third_person_owner(
        self,
        expression: str,
        frame: PerspectiveFrame,
        candidates: list[str],
    ) -> ReferenceResolution:
        valid = [candidate for candidate in candidates if self.registry.get(candidate)]
        if frame.focus_entity_id and self.registry.get(frame.focus_entity_id):
            return self._resolved(
                expression,
                frame.focus_entity_id,
                0.82,
                ["THIRD_PERSON_FOCUS_ENTITY"],
            )
        if len(valid) == 1:
            return self._resolved(
                expression,
                valid[0],
                0.76,
                ["THIRD_PERSON_SINGLE_CANDIDATE"],
            )
        if len(valid) > 1:
            return ReferenceResolution(
                expression=expression,
                status=ResolutionStatus.AMBIGUOUS,
                entity_ids=valid,
                confidence=0.45,
                reason_codes=["THIRD_PERSON_MULTIPLE_CANDIDATES"],
            )
        return self._unresolved(expression, ["THIRD_PERSON_NO_ANTECEDENT"])

    def _resolve_relation(
        self,
        expression: str,
        possessor_id: str,
        noun: str,
        base_confidence: float,
        reasons: list[str],
    ) -> ReferenceResolution:
        relation = self._kinship[noun]
        entity_ids = self._relation_entities(possessor_id, relation)
        if len(entity_ids) == 1:
            relation_confidence = self._relation_confidence(possessor_id, relation, entity_ids[0])
            return self._resolved(
                expression,
                entity_ids[0],
                min(base_confidence, relation_confidence),
                reasons + [f"RELATION_{relation.upper()}"],
            )
        if len(entity_ids) > 1:
            return ReferenceResolution(
                expression=expression,
                status=ResolutionStatus.AMBIGUOUS,
                entity_ids=entity_ids,
                confidence=min(base_confidence, 0.55),
                reason_codes=reasons + [f"MULTIPLE_{relation.upper()}_CANDIDATES"],
            )
        return self._unresolved(
            expression,
            reasons + [f"RELATION_{relation.upper()}_NOT_FOUND"],
        )

    def _relation_entities(self, possessor_id: str, relation: str) -> list[str]:
        if relation in {"mother_of", "father_of"}:
            return [
                item.source
                for item in self.registry.relationships
                if item.relation_type == relation and item.target == possessor_id
            ]
        if relation == "child_of":
            return [
                item.target
                for item in self.registry.relationships
                if item.relation_type in {"mother_of", "father_of"} and item.source == possessor_id
            ]
        return []

    def _relation_confidence(self, possessor_id: str, relation: str, entity_id: str) -> float:
        matches: list[float] = []
        for item in self.registry.relationships:
            if (
                relation in {"mother_of", "father_of"}
                and item.relation_type == relation
                and item.target == possessor_id
                and item.source == entity_id
            ):
                matches.append(item.confidence)
            if (
                relation == "child_of"
                and item.relation_type in {"mother_of", "father_of"}
                and item.source == possessor_id
                and item.target == entity_id
            ):
                matches.append(item.confidence)
        return max(matches, default=0.5)

    @staticmethod
    def _normalize(value: str) -> str:
        value = unicodedata.normalize("NFKC", value).strip().casefold()
        value = re.sub(r"^[\s\"“”'‘’]+|[\s\"“”'‘’.,!?;:]+$", "", value)
        return re.sub(r"\s+", " ", value)

    @staticmethod
    def _resolved(
        expression: str,
        entity_id: str,
        confidence: float,
        reasons: list[str],
    ) -> ReferenceResolution:
        return ReferenceResolution(
            expression=expression,
            status=ResolutionStatus.RESOLVED,
            entity_ids=[entity_id],
            confidence=confidence,
            reason_codes=reasons,
        )

    @staticmethod
    def _unresolved(expression: str, reasons: list[str]) -> ReferenceResolution:
        return ReferenceResolution(
            expression=expression,
            status=ResolutionStatus.UNRESOLVED,
            confidence=0.15,
            reason_codes=reasons,
        )
