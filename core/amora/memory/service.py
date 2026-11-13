from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from amora.events import PrivacyScope
from amora.time import Clock

from .gate import MemoryWriteGate
from .models import MemoryCandidate, MemoryHit, MemoryQuery, MemoryRecord, MemoryWriteDecision
from .store import MemoryStore


@dataclass(frozen=True, slots=True)
class RememberResult:
    decision: MemoryWriteDecision
    memory: MemoryRecord | None


class MemoryService:
    def __init__(self, store: MemoryStore, clock: Clock, gate: MemoryWriteGate | None = None) -> None:
        self.store = store
        self.clock = clock
        self.gate = gate or MemoryWriteGate()

    def remember(self, candidate: MemoryCandidate, *, record_id: UUID | None = None,
                 reflection_lease: tuple[str, str] | None = None) -> RememberResult:
        # A derived record cannot widen the disclosure permissions of its sources.
        # Missing provenance is not evidence of public authorization.
        if candidate.provenance.derived_from:
            parents = [self.get(source_id) for source_id in candidate.provenance.derived_from]
            if any(parent is None for parent in parents):
                return RememberResult(
                    decision=MemoryWriteDecision(store=False, score=0.0, threshold=1.0,
                        reason_codes=["DERIVED_SOURCE_UNAVAILABLE"]),
                    memory=None,
                )
            rank = {PrivacyScope.PUBLIC: 0, PrivacyScope.SOCIAL: 1, PrivacyScope.PERSONAL: 2,
                    PrivacyScope.PRIVATE: 3, PrivacyScope.SECRET: 4, PrivacyScope.AUTH: 5}
            scope = max([candidate.privacy_scope, *(parent.privacy_scope for parent in parents)],
                        key=rank.__getitem__)
            # Intersect effective audiences for all nonpublic sources.
            audiences = []
            for source in [candidate, *parents]:
                if source.privacy_scope == PrivacyScope.PUBLIC:
                    continue
                allowed = set(source.audience)
                if source.owner_entity_id:
                    allowed.add(source.owner_entity_id)
                elif not allowed and source.provenance.actor_id:
                    allowed.add(source.provenance.actor_id)
                audiences.append(allowed)
            audience = set.intersection(*audiences) if audiences else set()
            if scope != PrivacyScope.PUBLIC and not audience:
                return RememberResult(
                    decision=MemoryWriteDecision(store=False, score=0.0, threshold=1.0,
                        reason_codes=["DERIVED_AUDIENCE_EMPTY"]),
                    memory=None,
                )
            candidate = candidate.model_copy(update={
                "privacy_scope": scope,
                "audience": audience,
                "owner_entity_id": candidate.owner_entity_id if candidate.owner_entity_id in audience else None,
                "remote_processing_allowed": candidate.remote_processing_allowed and
                    all(parent.remote_processing_allowed for parent in parents),
            })
        decision = self.gate.evaluate(candidate)
        if not decision.store or decision.recommended_kind is None:
            return RememberResult(decision=decision, memory=None)

        if record_id is not None:
            existing = self.get(record_id)
            if existing is not None:
                return RememberResult(decision=decision, memory=existing)
        now = self.clock.now()
        remote_allowed = candidate.remote_processing_allowed
        if candidate.privacy_scope in {PrivacyScope.PRIVATE, PrivacyScope.SECRET, PrivacyScope.AUTH}:
            remote_allowed = False

        record = MemoryRecord(
            **({"id": record_id} if record_id is not None else {}),
            kind=decision.recommended_kind,
            content=candidate.content.strip(),
            summary=candidate.summary.strip() if candidate.summary else None,
            provenance=candidate.provenance,
            privacy_scope=candidate.privacy_scope,
            owner_entity_id=candidate.owner_entity_id,
            audience=candidate.audience,
            remote_processing_allowed=remote_allowed,
            entity_refs=candidate.entity_refs,
            tags=candidate.tags,
            valid_from=candidate.valid_from,
            valid_until=candidate.valid_until,
            created_at=now,
            updated_at=now,
            importance=candidate.importance,
            gate_score=decision.score,
            metadata=candidate.metadata,
        )
        self.store.put(record, reflection_lease=reflection_lease)
        return RememberResult(decision=decision, memory=record)

    def recall(self, query: MemoryQuery) -> list[MemoryHit]:
        return self.store.search(query)

    def get(self, memory_id: UUID | str) -> MemoryRecord | None:
        return self.store.get(memory_id)

    def forget_softly(self, memory_id: UUID | str, factor: float = 0.8) -> float | None:
        return self.store.degrade_retrievability(memory_id, factor)

    def archive(self, memory_id: UUID | str) -> bool:
        return self.store.archive(memory_id)

    def close(self) -> None:
        self.store.close()
