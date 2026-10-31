from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from .perspective import PerspectiveResolver, PerspectiveStack, ResolutionStatus


class ReferenceInput(BaseModel):
    expression: str
    candidates: list[str] = Field(default_factory=list)


class SemanticDraft(BaseModel):
    intent: str
    proposition_type: str
    roles: dict[str, Any] = Field(default_factory=dict)


class UnresolvedReference(BaseModel):
    role: str
    expression: str
    candidates: list[str] = Field(default_factory=list)
    confidence: float
    reason_codes: list[str] = Field(default_factory=list)


class ACIRProposition(BaseModel):
    type: str
    roles: dict[str, Any] = Field(default_factory=dict)


class ACIR(BaseModel):
    speaker: str
    addressee: str | None
    intent: str
    entities: list[str] = Field(default_factory=list)
    proposition: ACIRProposition
    unresolved_references: list[UnresolvedReference] = Field(default_factory=list)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class ACIRCompiler:
    """Compile typed semantic input into provider-independent canonical form."""

    def __init__(self, resolver: PerspectiveResolver) -> None:
        self.resolver = resolver

    def compile_utterance(self, text: str, stack: PerspectiveStack) -> ACIR:
        """Compile the conservative ACIR envelope available for every utterance.

        This deliberately does not pretend that the Core already has a general
        semantic parser. The raw utterance remains raw content while the deictic
        participants (speaker/addressee) are resolved through the same canonical
        PerspectiveResolver used by richer typed SemanticDraft callers.
        """
        roles: dict[str, Any] = {
            "speaker": ReferenceInput(expression="eu"),
            "content": text,
        }
        if stack.current.addressee_id is not None:
            roles["addressee"] = ReferenceInput(expression="você")
        return self.compile(
            SemanticDraft(
                intent="communicate",
                proposition_type="utterance",
                roles=roles,
            ),
            stack,
        )

    def compile(self, draft: SemanticDraft, stack: PerspectiveStack) -> ACIR:
        frame = stack.current
        entities = {frame.speaker_id}
        if frame.addressee_id:
            entities.add(frame.addressee_id)

        roles: dict[str, Any] = {}
        unresolved: list[UnresolvedReference] = []
        confidence = 1.0

        for role, value in draft.roles.items():
            if isinstance(value, ReferenceInput):
                resolution = self.resolver.resolve(
                    value.expression,
                    stack,
                    candidates=value.candidates,
                )
                confidence = min(confidence, resolution.confidence)
                if resolution.status == ResolutionStatus.RESOLVED and resolution.entity_id:
                    roles[role] = resolution.entity_id
                    entities.add(resolution.entity_id)
                else:
                    roles[role] = {
                        "unresolved_reference": value.expression,
                        "candidates": resolution.entity_ids,
                    }
                    unresolved.append(
                        UnresolvedReference(
                            role=role,
                            expression=value.expression,
                            candidates=resolution.entity_ids,
                            confidence=resolution.confidence,
                            reason_codes=resolution.reason_codes,
                        )
                    )
            else:
                roles[role] = value

        return ACIR(
            speaker=frame.speaker_id,
            addressee=frame.addressee_id,
            intent=draft.intent,
            entities=sorted(entities),
            proposition=ACIRProposition(type=draft.proposition_type, roles=roles),
            unresolved_references=unresolved,
            confidence=confidence,
        )
