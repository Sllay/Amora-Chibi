"""Opt-in verified memory reads; canonical store remains the write authority.

No default activation. A caller must supply an exclusively owned, certified
read group with an authoritative revision and a replica-backed MemoryStore.
"""
from __future__ import annotations

import sqlite3

from amora.latency_trace import increment, trace_stage
from amora.storage.replica_coherence import ReplicaCoherenceError, VersionedReplicaReadGroup

from .models import MemoryHit, MemoryQuery
from .store import MemoryStore


class ReplicaMemoryReadView(MemoryStore):
    """Reuse the same ranking and filters; never run schema creation on a view."""
    def _ensure_fts(self):
        # Capability must be certified against the real primary before injection.
        return True

    def search(self, query):
        raise sqlite3.OperationalError("REPLICA_USE_PURE_RANK")


class VerifiedMemoryRecall:
    def __init__(self, primary: MemoryStore, view: ReplicaMemoryReadView,
                 group: VersionedReplicaReadGroup):
        if view.backend is not group.backend or primary.backend is group.backend:
            raise ValueError("REPLICA_MEMORY_AUTHORITY_MISMATCH")
        if primary.identities is not view.identities or primary.clock is not view.clock:
            raise ValueError("REPLICA_MEMORY_CONTEXT_MISMATCH")
        self.primary, self.view, self.group = primary, view, group

    @trace_stage("memory_projection")
    def recall(self, query: MemoryQuery) -> list[MemoryHit]:
        # Only ordinary, recipient-scoped remote projections are eligible.
        # Explicit recall, group/local/private controls retain the caller's
        # canonical path. Never relax query filters to obtain a cache hit.
        if (not query.projection_recipient_id or not query.projection_remote_only
                or query.projection_public_only or query.include_archived):
            return self.primary.search(query)
        try:
            verified = self.group.read_verified(lambda: self.view.rank(query))
        except sqlite3.OperationalError:
            # Only a read failed; no access write or external effect occurred.
            increment("pcs_memory_projection_read_fallback")
            return self.primary.search(query)
        try:
            hits = self.primary.record_verified_access(verified.value, verified.stamp)
        except ReplicaCoherenceError:
            # A definite primary mismatch/rolled-back access transaction.
            # Do not replay a transport/commit failure with unknown outcome.
            self.group.invalidate()
            increment("pcs_memory_projection_revision_fallback")
            return self.primary.search(query)
        increment("pcs_memory_projection_used")
        return hits
