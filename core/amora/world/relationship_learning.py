from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from amora.identity import EntityRegistry
from amora.time import Clock

from .models import EpistemicKind, EvidenceKind, WorldClaim
from .service import RelationshipEvolutionService, WorldModelService


class CognitiveTurnStore(Protocol):
    def list_messages(
        self,
        *,
        limit: int = 40,
        speaker_id: str | None = None,
        turn_id: object | None = None,
    ) -> list[Any]: ...


@dataclass(frozen=True, slots=True)
class RelationshipLearningDecision:
    learned: bool
    reason_code: str
    evidence_count: int = 0
    confidence: float | None = None


class RelationshipLearningService:
    """Promote corroborated relationship hypotheses into temporal identity edges.

    Provider reflection remains non-canonical. A relationship is learned only
    after multiple independent observed events agree on the same canonical
    endpoints and a bounded, locally-known relationship predicate. Technical
    turns and turns with ``relationship_learning=false`` never contribute.

    Parenthood claims about Amora have an additional authority gate: repetition
    from an unknown speaker may remain a hypothesis, but cannot promote itself
    into the canonical Relationship Graph.
    """

    LEARNABLE_RELATIONS = frozenset({"father_of", "mother_of", "child_of"})
    MIN_DISTINCT_EVENTS = 2
    MIN_MEAN_CONFIDENCE = 0.78
    INPUT_SOURCES = frozenset({"stt", "text"})

    def __init__(
        self,
        *,
        world: WorldModelService,
        relationships: RelationshipEvolutionService,
        identities: EntityRegistry,
        cognitive_store: CognitiveTurnStore,
        clock: Clock,
        family_authority_ids: frozenset[str] | None = None,
    ) -> None:
        self.world = world
        self.relationships = relationships
        self.identities = identities
        self.cognitive_store = cognitive_store
        self.clock = clock
        self.family_authority_ids = family_authority_ids or frozenset({"PERSON_BK"})
        self._learned_count = 0
        self._last_decision: RelationshipLearningDecision | None = None

    def state(self) -> dict[str, object]:
        decision = self._last_decision
        return {
            "learned_count": self._learned_count,
            "last_reason_code": decision.reason_code if decision else None,
            "last_evidence_count": decision.evidence_count if decision else 0,
            "last_confidence": decision.confidence if decision else None,
            "learnable_relations": sorted(self.LEARNABLE_RELATIONS),
            "min_distinct_events": self.MIN_DISTINCT_EVENTS,
            "min_mean_confidence": self.MIN_MEAN_CONFIDENCE,
            "family_authority_ids": sorted(self.family_authority_ids),
        }

    def observe_claim(self, claim: WorldClaim) -> RelationshipLearningDecision:
        decision = self._evaluate_and_maybe_learn(claim)
        self._last_decision = decision
        if decision.learned:
            self._learned_count += 1
        return decision

    def _evaluate_and_maybe_learn(self, claim: WorldClaim) -> RelationshipLearningDecision:
        if claim.kind != EpistemicKind.HYPOTHESIS or not claim.source.startswith("cognition:"):
            return RelationshipLearningDecision(False, "NOT_COGNITIVE_RELATIONSHIP_HYPOTHESIS")
        if claim.predicate not in self.LEARNABLE_RELATIONS:
            return RelationshipLearningDecision(False, "RELATIONSHIP_PREDICATE_NOT_LEARNABLE")
        if not isinstance(claim.subject_id, str) or self.identities.get(claim.subject_id) is None:
            return RelationshipLearningDecision(False, "RELATIONSHIP_SUBJECT_NOT_CANONICAL")
        if not isinstance(claim.object_value, str) or self.identities.get(claim.object_value) is None:
            return RelationshipLearningDecision(False, "RELATIONSHIP_OBJECT_NOT_CANONICAL")
        if claim.subject_id == claim.object_value:
            return RelationshipLearningDecision(False, "RELATIONSHIP_SELF_EDGE_REJECTED")

        current_turn = self._eligible_turn(claim)
        if current_turn is None:
            return RelationshipLearningDecision(False, "RELATIONSHIP_TURN_NOT_ELIGIBLE")

        if self._defines_amora_parenthood(claim):
            speaker_id = getattr(current_turn, "speaker_id", None)
            if speaker_id not in self.family_authority_ids:
                return RelationshipLearningDecision(False, "AMORA_FAMILY_AUTHORITY_REQUIRED")

        # Keep the first implementation deliberately local to the current social
        # pair. This prevents a model from manufacturing third-party family graphs
        # out of unrelated context or recalled memory.
        active_pair = {current_turn.speaker_id, current_turn.addressee_id}
        if claim.subject_id not in active_pair or claim.object_value not in active_pair:
            return RelationshipLearningDecision(False, "RELATIONSHIP_ENDPOINT_OUTSIDE_ACTIVE_PAIR")

        evidence_by_event: dict[str, tuple[float, str]] = {}
        now = self.clock.now()
        for candidate in self.world.history(subject_id=claim.subject_id, predicate=claim.predicate, limit=100):
            if candidate.kind != EpistemicKind.HYPOTHESIS:
                continue
            if candidate.object_value != claim.object_value:
                continue
            if not candidate.source.startswith("cognition:"):
                continue
            if candidate.valid_until is not None and candidate.valid_until <= now:
                continue
            if self._eligible_turn(candidate) is None:
                continue
            for evidence in candidate.evidence:
                if evidence.kind != EvidenceKind.EVENT or not evidence.ref_id:
                    continue
                previous = evidence_by_event.get(evidence.ref_id)
                score = min(candidate.confidence, evidence.confidence)
                if previous is None or score > previous[0]:
                    evidence_by_event[evidence.ref_id] = (score, str(candidate.id))

        if len(evidence_by_event) < self.MIN_DISTINCT_EVENTS:
            return RelationshipLearningDecision(
                False,
                "RELATIONSHIP_NEEDS_CORROBORATION",
                evidence_count=len(evidence_by_event),
            )

        scores = [item[0] for item in evidence_by_event.values()]
        mean_confidence = sum(scores) / len(scores)
        if mean_confidence < self.MIN_MEAN_CONFIDENCE:
            return RelationshipLearningDecision(
                False,
                "RELATIONSHIP_CONFIDENCE_BELOW_THRESHOLD",
                evidence_count=len(scores),
                confidence=mean_confidence,
            )

        learned_confidence = min(0.97, mean_confidence + min(0.10, 0.04 * (len(scores) - 1)))
        existing = self.relationships.store.current_relationship(
            claim.subject_id,
            claim.predicate,
            claim.object_value,
            at=now,
        )
        if existing is not None and existing.confidence >= learned_confidence:
            return RelationshipLearningDecision(
                False,
                "RELATIONSHIP_EXISTING_STATE_STRONGER",
                evidence_count=len(scores),
                confidence=learned_confidence,
            )

        sorted_evidence = sorted(evidence_by_event.items())
        self.relationships.record(
            claim.subject_id,
            claim.predicate,
            claim.object_value,
            confidence=learned_confidence,
            source_of_truth="cognition:corroborated_relationship",
            effective_at=now,
            metadata={
                "algorithm": "relationship_learning_v1",
                "evidence_event_ids": [event_id for event_id, _ in sorted_evidence],
                "hypothesis_ids": sorted({claim_id for _, (_, claim_id) in sorted_evidence}),
                "mean_hypothesis_confidence": mean_confidence,
            },
        )
        return RelationshipLearningDecision(
            True,
            "RELATIONSHIP_CORROBORATED_AND_LEARNED",
            evidence_count=len(scores),
            confidence=learned_confidence,
        )

    @staticmethod
    def _defines_amora_parenthood(claim: WorldClaim) -> bool:
        if claim.predicate in {"father_of", "mother_of"}:
            return claim.object_value == "ENTITY_AMORA"
        if claim.predicate == "child_of":
            return claim.subject_id == "ENTITY_AMORA"
        return False

    def _eligible_turn(self, claim: WorldClaim):
        turn_id = str(claim.metadata.get("turn_id") or "")
        if not turn_id:
            return None
        messages = self.cognitive_store.list_messages(limit=8, turn_id=turn_id)
        for message in messages:
            source = getattr(message, "source", None)
            source_value = str(getattr(source, "value", source) or "")
            # Only the observed inbound utterance may authorize relationship
            # learning. Generated cognitive/TTS messages from the same turn must
            # never bypass a disabled/test input gate.
            if source_value not in self.INPUT_SOURCES:
                continue
            if getattr(message, "speaker_id", None) in {None, "ENTITY_AMORA"}:
                continue
            metadata = dict(getattr(message, "metadata", {}) or {})
            if metadata.get("test_mode") is True:
                return None
            if metadata.get("relationship_learning") is False:
                return None
            return message
        return None
