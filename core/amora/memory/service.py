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

    def remember(self, candidate: MemoryCandidate) -> RememberResult:
        decision = self.gate.evaluate(candidate)
        if not decision.store or decision.recommended_kind is None:
            return RememberResult(decision=decision, memory=None)

        now = self.clock.now()
        remote_allowed = candidate.remote_processing_allowed
        if candidate.privacy_scope in {PrivacyScope.PRIVATE, PrivacyScope.SECRET, PrivacyScope.AUTH}:
            remote_allowed = False

        record = MemoryRecord(
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
        self.store.put(record)
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
