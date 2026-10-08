from __future__ import annotations

from datetime import timedelta
from typing import Any

from amora.events import PrivacyScope
from amora.identity import EntityRegistry
from amora.time import Clock

from .models import (
    EpistemicKind,
    EvidenceKind,
    EvidenceRef,
    RelationshipState,
    SelfEntry,
    SelfLayer,
    WorldClaim,
    WorldClaimDraft,
    WorldQuery,
    WorldWriteResult,
)
from .store import WorldStore


class WorldModelService:
    CANONICAL_BELIEF_THRESHOLD = 0.90
    DEFAULT_HYPOTHESIS_TTL = timedelta(hours=24)

    def __init__(self, store: WorldStore, clock: Clock) -> None:
        self.store = store
        self.clock = clock

    def record(self, draft: WorldClaimDraft) -> WorldWriteResult:
        if draft.privacy_scope == PrivacyScope.AUTH:
            raise ValueError("AUTH content cannot enter the World Model")

        now = self.clock.now()
        valid_from = draft.valid_from or now
        valid_until = draft.valid_until
        reasons: list[str] = [f"KIND_{draft.kind.value.upper()}"]

        if draft.kind == EpistemicKind.HYPOTHESIS and valid_until is None:
            valid_until = valid_from + self.DEFAULT_HYPOTHESIS_TTL
            reasons.append("HYPOTHESIS_TTL_APPLIED")

        canonical = draft.kind == EpistemicKind.BELIEF and draft.confidence >= self.CANONICAL_BELIEF_THRESHOLD
        if canonical:
            reasons.append("CANONICAL_BELIEF")
        elif draft.kind == EpistemicKind.BELIEF:
            reasons.append("BELIEF_BELOW_CANONICAL_THRESHOLD")
        else:
            reasons.append("NONCANONICAL_EPISTEMIC_RECORD")

        previous: WorldClaim | None = None
        if canonical:
            previous = self.store.active_belief(
                subject_id=draft.subject_id,
                predicate=draft.predicate,
                context_key=draft.context_key,
                at=valid_from,
            )
            if previous is not None and valid_from < previous.valid_from:
                raise ValueError("belief revision cannot start before the active belief")
            if previous is not None:
                self.store.expire_claim(previous.id, valid_from)
                reasons.append("BELIEF_REVISION")
                if previous.object_value != draft.object_value:
                    reasons.append("EXPECTATION_CHANGED")
                else:
                    reasons.append("BELIEF_REAFFIRMED")

        claim = WorldClaim(
            kind=draft.kind,
            statement=draft.statement,
            subject_id=draft.subject_id,
            predicate=draft.predicate,
            object_value=draft.object_value,
            context_key=draft.context_key,
            source=draft.source,
            source_entity_id=draft.source_entity_id,
            confidence=draft.confidence,
            evidence=draft.evidence,
            valid_from=valid_from,
            valid_until=valid_until,
            created_at=now,
            updated_at=now,
            canonical=canonical,
            supersedes_claim_id=previous.id if previous else None,
            privacy_scope=draft.privacy_scope,
            remote_processing_allowed=draft.remote_processing_allowed,
            metadata=draft.metadata,
        )
        self.store.put_claim(claim)
        return WorldWriteResult(
            claim=claim,
            reason_codes=reasons,
            revised_claim_id=previous.id if previous else None,
        )

    def promote_to_belief(
        self,
        claim_id: str,
        *,
        confidence: float,
        source: str = "world:promotion",
        statement: str | None = None,
    ) -> WorldWriteResult:
        source_claim = self.store.get_claim(claim_id)
        if source_claim is None:
            raise KeyError(claim_id)
        evidence = list(source_claim.evidence)
        evidence.append(EvidenceRef(kind=EvidenceKind.CLAIM, ref_id=str(source_claim.id), confidence=source_claim.confidence))
        return self.record(
            WorldClaimDraft(
                kind=EpistemicKind.BELIEF,
                statement=statement or source_claim.statement,
                subject_id=source_claim.subject_id,
                predicate=source_claim.predicate,
                object_value=source_claim.object_value,
                context_key=source_claim.context_key,
                source=source,
                source_entity_id=source_claim.source_entity_id,
                confidence=confidence,
                evidence=evidence,
                valid_from=self.clock.now(),
                privacy_scope=source_claim.privacy_scope,
                remote_processing_allowed=source_claim.remote_processing_allowed,
                metadata={"promoted_from_kind": source_claim.kind.value},
            )
        )

    def current_state(self, *, subject_id: str | None = None) -> list[WorldClaim]:
        return self.store.current_state(subject_id=subject_id)

    def history(self, *, subject_id: str | None = None, predicate: str | None = None, limit: int = 100) -> list[WorldClaim]:
        return self.store.query_claims(
            WorldQuery(
                subject_id=subject_id,
                predicate=predicate,
                include_expired=True,
                canonical_only=False,
                limit=limit,
            )
        )


class SelfModelService:
    """Persistent layered Self Model. Core Identity is protected from casual mutation."""

    def __init__(self, store: WorldStore, clock: Clock) -> None:
        self.store = store
        self.clock = clock

    def ensure_core_identity(self) -> None:
        defaults: dict[str, Any] = {
            "entity_id": "ENTITY_AMORA",
            "canonical_name": "Amora",
            "entity_type": "ai_persona",
        }
        for key, value in defaults.items():
            if self.store.get_self_entry(SelfLayer.CORE_IDENTITY, key) is None:
                now = self.clock.now()
                self.store.upsert_self_entry(
                    SelfEntry(
                        layer=SelfLayer.CORE_IDENTITY,
                        key=key,
                        value=value,
                        confidence=1.0,
                        source="bootstrap:canonical_identity",
                        locked=True,
                        created_at=now,
                        updated_at=now,
                    )
                )

    def set(
        self,
        layer: SelfLayer,
        key: str,
        value: Any,
        *,
        source: str,
        confidence: float = 1.0,
        allow_core_override: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> SelfEntry:
        current = self.store.get_self_entry(layer, key)
        if current and current.locked and current.value != value and not allow_core_override:
            raise PermissionError(f"protected Self Model entry: {layer.value}.{key}")
        now = self.clock.now()
        entry = SelfEntry(
            layer=layer,
            key=key,
            value=value,
            confidence=confidence,
            source=source,
            locked=current.locked if current else layer == SelfLayer.CORE_IDENTITY,
            created_at=current.created_at if current else now,
            updated_at=now,
            metadata=metadata or {},
        )
        return self.store.upsert_self_entry(entry)

    def get(self, layer: SelfLayer, key: str) -> SelfEntry | None:
        return self.store.get_self_entry(layer, key)

    def snapshot(self) -> dict[str, dict[str, Any]]:
        result: dict[str, dict[str, Any]] = {}
        for entry in self.store.list_self_entries():
            result.setdefault(entry.layer.value, {})[entry.key] = entry.value
        return result


class RelationshipEvolutionService:
    """Temporal relationship identity/provenance history; affect belongs to V0.5."""

    def __init__(self, store: WorldStore, clock: Clock, identities: EntityRegistry) -> None:
        self.store = store
        self.clock = clock
        self.identities = identities

    def seed_registry_relationships(self) -> None:
        now = self.clock.now()
        for relation in self.identities.relationships:
            if self.store.current_relationship(relation.source, relation.relation_type, relation.target, at=now) is None:
                self.store.put_relationship(
                    RelationshipState(
                        source_id=relation.source,
                        relation_type=relation.relation_type,
                        target_id=relation.target,
                        confidence=relation.confidence,
                        source_of_truth="bootstrap:entity_registry",
                        valid_from=now,
                        created_at=now,
                    )
                )

    def record(
        self,
        source_id: str,
        relation_type: str,
        target_id: str,
        *,
        confidence: float,
        source_of_truth: str,
        effective_at=None,
        metadata: dict[str, Any] | None = None,
    ) -> RelationshipState:
        if self.identities.get(source_id) is None or self.identities.get(target_id) is None:
            raise ValueError("relationship endpoints must be canonical registered entities")
        when = effective_at or self.clock.now()
        current = self.store.current_relationship(source_id, relation_type, target_id, at=when)
        if current and current.confidence == confidence and current.source_of_truth == source_of_truth and (metadata or {}) == current.metadata:
            return current
        if current:
            if when < current.valid_from:
                raise ValueError("relationship revision cannot predate current state")
            self.store.expire_relationship(current.id, when)
        relation = RelationshipState(
            source_id=source_id,
            relation_type=relation_type,
            target_id=target_id,
            confidence=confidence,
            source_of_truth=source_of_truth,
            valid_from=when,
            created_at=self.clock.now(),
            supersedes_id=current.id if current else None,
            metadata=metadata or {},
        )
        self.store.put_relationship(relation)
        return relation

    def history(self, source_id: str, *, target_id: str | None = None) -> list[RelationshipState]:
        return self.store.relationship_history(source_id, target_id=target_id)
