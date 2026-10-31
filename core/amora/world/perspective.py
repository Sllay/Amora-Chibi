from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from amora.events import PrivacyScope
from amora.language import ACIR

from .models import (
    EpistemicKind,
    EvidenceRef,
    PerspectiveConsolidationResult,
    PerspectiveConsolidationStatus,
    WorldClaimDraft,
)
from .service import WorldModelService


class PerspectiveClaimDraft(BaseModel):
    kind: EpistemicKind = EpistemicKind.REPORT
    statement: str = Field(min_length=1, max_length=12000)
    predicate: str = Field(min_length=1, max_length=160)
    subject_role: str = "subject"
    object_role: str = "object"
    context_key: str = ""
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: str = "acir:perspective_consolidation"
    source_entity_id: str | None = None
    evidence: list[EvidenceRef] = Field(default_factory=list)
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    remote_processing_allowed: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class PerspectiveConsolidator:
    """Promote only perspective-resolved ACIR propositions into epistemic records.

    The consolidator is intentionally a persistence boundary, not a parser. The
    caller must first build a typed ACIR proposition whose references were
    resolved locally. Unresolved or non-canonical subjects are deferred rather
    than silently rewritten into subject-less durable claims.
    """

    def __init__(self, world: WorldModelService) -> None:
        self.world = world

    def consolidate(self, acir: ACIR, draft: PerspectiveClaimDraft) -> PerspectiveConsolidationResult:
        if acir.unresolved_references:
            return PerspectiveConsolidationResult(
                status=PerspectiveConsolidationStatus.DEFERRED,
                reason_codes=["UNRESOLVED_REFERENCES", "NO_DURABLE_WORLD_CONSOLIDATION"],
            )

        roles = acir.proposition.roles
        subject = roles.get(draft.subject_role)
        if not isinstance(subject, str) or not subject:
            return PerspectiveConsolidationResult(
                status=PerspectiveConsolidationStatus.DEFERRED,
                reason_codes=["SUBJECT_NOT_CANONICAL", "NO_DURABLE_WORLD_CONSOLIDATION"],
            )

        object_value = roles.get(draft.object_role)
        if isinstance(object_value, dict) and "unresolved_reference" in object_value:
            return PerspectiveConsolidationResult(
                status=PerspectiveConsolidationStatus.DEFERRED,
                reason_codes=["OBJECT_NOT_CANONICAL", "NO_DURABLE_WORLD_CONSOLIDATION"],
            )

        confidence = min(acir.confidence, draft.confidence)
        result = self.world.record(
            WorldClaimDraft(
                kind=draft.kind,
                statement=draft.statement,
                subject_id=subject,
                predicate=draft.predicate,
                object_value=object_value,
                context_key=draft.context_key,
                source=draft.source,
                source_entity_id=draft.source_entity_id or acir.speaker,
                confidence=confidence,
                evidence=draft.evidence,
                privacy_scope=draft.privacy_scope,
                remote_processing_allowed=draft.remote_processing_allowed,
                metadata={
                    **draft.metadata,
                    "acir_intent": acir.intent,
                    "acir_proposition_type": acir.proposition.type,
                    "perspective_consolidated": True,
                },
            )
        )
        return PerspectiveConsolidationResult(
            status=PerspectiveConsolidationStatus.CONSOLIDATED,
            claim=result.claim,
            reason_codes=["PERSPECTIVE_RESOLVED", *result.reason_codes],
        )
