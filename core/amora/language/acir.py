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

    def compile_utterance(self, text: str, stack: PerspectiveStack, *, antecedents: list[str] | None = None) -> ACIR:
        """Compile the conservative ACIR envelope available for every utterance.

        This is bounded reference annotation, not a general semantic parser.
        The raw utterance remains content; participant and reference identities
        use the same PerspectiveResolver as typed SemanticDraft callers. A
        resolved reference never verifies the assertion made about that entity.
        """
        roles: dict[str, Any] = {
            "speaker": ReferenceInput(expression="eu"),
            "content": text,
        }
        if stack.current.addressee_id is not None:
            roles["addressee"] = ReferenceInput(expression="você")
        from .text_references import extract_references
        references = extract_references(text, stack, self.resolver, antecedents=antecedents)
        if references:
            roles["references"] = references
            roles["assertion_truth"] = "unverified_user_claim"
        result = self.compile(
            SemanticDraft(
                intent="communicate",
                proposition_type="utterance",
                roles=roles,
            ),
            stack,
        )
        result.entities = sorted(set(result.entities) | {entity_id for ref in references
                                 if ref["status"] == "resolved" for entity_id in ref["entity_ids"]})
        return result

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
