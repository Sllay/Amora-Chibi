"""D.3C.2A — durable episode work repository, not a live worker.

Requires the isolated candidate DDL. No schema bootstrap, provider, scheduling
or memory writes. C.2B must fence consolidation in the memory transaction.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from amora.latency_trace import trace_lock
from amora.storage.backend import execute_atomic_statement

from .episode_fence import EpisodeWriteLease
from .episodic_observations import (
    EpisodeSelection, EpisodicObservationConsolidator, episode_record_id,
)


class EnqueueResult(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    LOW_SIGNAL = "low_signal"
    CAPACITY = "capacity"


class JobOutcome(StrEnum):
    STORED = "stored"
    DUPLICATE = "duplicate"
    REJECTED = "rejected"
    TRANSIENT_FAILURE = "transient_failure"
    PERMANENT_FAILURE = "permanent_failure"


@dataclass(frozen=True, slots=True)
class EpisodeClaim:
    job_id: UUID
    selection: EpisodeSelection
    session_id: str
    correlation_id: UUID
    lease_token: UUID
    attempts: int


class EpisodeWorkRepository:
    """References selected observations; does not copy semantic/media content."""

    def __init__(self, consolidator: EpisodicObservationConsolidator, *,
                 max_active: int = 128, max_active_per_session: int = 8,
                 max_attempts: int = 5, lease_seconds: int = 120):
        limits = ((max_active, 1, 1024), (max_active_per_session, 1, 64),
                  (max_attempts, 1, 8), (lease_seconds, 1, 300))
        if any(type(v) is not int or not lo <= v <= hi for v, lo, hi in limits):
            raise ValueError("EPISODE_WORK_LIMIT_INVALID")
        if max_active_per_session > max_active:
            raise ValueError("EPISODE_WORK_LIMIT_INVALID")
        self.consolidator = consolidator
        self.database = consolidator.database
        self.clock = consolidator.memory.clock
        self.max_active = max_active
        self.max_active_per_session = max_active_per_session
        self.max_attempts = max_attempts
        self.lease_seconds = lease_seconds

    def _now(self):
        return self.clock.now().astimezone(timezone.utc)

    def enqueue(self, selection: EpisodeSelection) -> EnqueueResult:
        """Validate an internally authorized selection, then create one job."""
        selection = EpisodeSelection.model_validate(selection.model_dump(mode="python"))
        selection = selection.model_copy(update={"evidence_ids": tuple(sorted(selection.evidence_ids))})
        job_id, candidate = self.consolidator.build_candidate(selection)
        decision = self.consolidator.memory.gate.evaluate(candidate)
        if not decision.store or decision.recommended_kind is None:
            return EnqueueResult.LOW_SIGNAL
        session_id = candidate.provenance.session_id
        correlation_id = candidate.metadata["correlation_id"]
        encoded = selection.model_dump_json()
        db = self.database
        with trace_lock(db._lock), db._conn:
            for event_id in selection.evidence_ids:
                if db._conn.execute("SELECT 1 FROM sensory_episode_invalidations WHERE event_id=?",
                                    (str(event_id),)).fetchone() is not None:
                    raise ValueError("EPISODE_EVIDENCE_INVALIDATED")
            if db._conn.execute("SELECT 1 FROM sensory_episode_jobs WHERE job_id=?",
                                (str(job_id),)).fetchone() is not None:
                return EnqueueResult.DUPLICATE
            active = db._conn.execute(
                "SELECT COUNT(*) FROM sensory_episode_jobs WHERE status IN ('pending','claimed')"
            ).fetchone()[0]
            scoped = db._conn.execute(
                "SELECT COUNT(*) FROM sensory_episode_jobs WHERE correlation_id=? "
                "AND status IN ('pending','claimed')", (correlation_id,),
            ).fetchone()[0]
            if active >= self.max_active or scoped >= self.max_active_per_session:
                return EnqueueResult.CAPACITY
            now = self._now().isoformat()
            inserted = db._conn.execute(
                "INSERT INTO sensory_episode_jobs(job_id,selection_json,session_id,correlation_id,"
                "status,created_at,available_at) VALUES (?,?,?,?,'pending',?,?) "
                "ON CONFLICT(job_id) DO NOTHING",
                (str(job_id), encoded, session_id, correlation_id, now, now),
            )
            if inserted.rowcount == 0:
                return EnqueueResult.DUPLICATE
            if inserted.rowcount != 1:
                raise RuntimeError("EPISODE_WORK_INSERT_UNKNOWN")
            db._conn.executemany(
                "INSERT INTO sensory_episode_job_evidence(job_id,event_id) VALUES (?,?)",
                [(str(job_id), str(event_id)) for event_id in selection.evidence_ids],
            )
            return EnqueueResult.ACCEPTED

    def claim(self) -> EpisodeClaim | None:
        """Claim one due job; expired leases are retried with bounded attempts."""
        db = self.database
        with trace_lock(db._lock), db._conn:
            now = self._now()
            stamp = now.isoformat()
            # Bounded sweep: never leave crashed final attempts leased forever.
            db._conn.execute(
                "UPDATE sensory_episode_jobs SET status='failed',lease_token=NULL,lease_until=NULL,"
                "last_reason='EPISODE_ATTEMPTS_EXHAUSTED' WHERE job_id IN ("
                "SELECT job_id FROM sensory_episode_jobs WHERE status='claimed' "
                "AND lease_until<=? AND attempts>=? ORDER BY lease_until,job_id LIMIT 32)",
                (stamp, self.max_attempts),
            )
            row = db._conn.execute(
                "SELECT job_id,selection_json,session_id,correlation_id,attempts FROM sensory_episode_jobs "
                "WHERE attempts<? AND ((status='pending' AND available_at<=?) OR "
                "(status='claimed' AND lease_until<=?)) ORDER BY available_at,created_at,job_id LIMIT 1",
                (self.max_attempts, stamp, stamp),
            ).fetchone()
            if row is None:
                return None
            try:
                job_id = UUID(row["job_id"])
                selection = EpisodeSelection.model_validate_json(row["selection_json"])
                correlation_id = UUID(row["correlation_id"])
                links = {r[0] for r in db._conn.execute(
                    "SELECT event_id FROM sensory_episode_job_evidence WHERE job_id=?", (str(job_id),))}
                if (episode_record_id(selection.evidence_ids) != job_id
                        or len(set(selection.evidence_ids)) != len(selection.evidence_ids)
                        or links != {str(event_id) for event_id in selection.evidence_ids}
                        or not row["session_id"]):
                    raise ValueError("invalid job")
            except (ValueError, TypeError, KeyError):
                db._conn.execute(
                    "UPDATE sensory_episode_jobs SET status='failed',lease_token=NULL,lease_until=NULL,"
                    "last_reason='EPISODE_WORK_CORRUPT' WHERE job_id=?", (row["job_id"],),
                )
                return None
            token = uuid4()
            until = (now + timedelta(seconds=self.lease_seconds)).isoformat()
            won = db._conn.execute(
                "UPDATE sensory_episode_jobs SET status='claimed',attempts=attempts+1,"
                "lease_token=?,lease_until=? WHERE job_id=? AND attempts=? AND attempts<? AND "
                "((status='pending' AND available_at<=?) OR (status='claimed' AND lease_until<=?))",
                (str(token), until, str(job_id), row["attempts"], self.max_attempts, stamp, stamp),
            )
            if won.rowcount != 1:
                return None
            return EpisodeClaim(job_id, selection, row["session_id"], correlation_id,
                                token, int(row["attempts"]) + 1)

    def process(self, claim: EpisodeClaim):
        """Reconstruct authorized evidence; memory write and success ACK are atomic.

        Storage errors propagate. Caller may retry using a current lease; a
        lost commit response is resolved by the durable job status.
        """
        if not isinstance(claim, EpisodeClaim):
            raise ValueError("EPISODE_WORK_CLAIM_INVALID")
        selection = EpisodeSelection.model_validate(claim.selection.model_dump(mode="python"))
        record_id, candidate = self.consolidator.build_candidate(selection)
        if (record_id != claim.job_id or candidate.provenance.session_id != claim.session_id
                or candidate.metadata["correlation_id"] != str(claim.correlation_id)):
            raise ValueError("EPISODE_WORK_CLAIM_INVALID")
        lease = EpisodeWriteLease(claim.job_id, claim.lease_token, claim.attempts,
                                  selection.model_dump_json())
        result = self.consolidator.memory.remember(
            candidate, record_id=record_id, insert_only=True, episode_lease=lease,
        )
        if result.memory is None and result.decision.reason_codes != ["MEMORY_CREATE_ALREADY_EXISTS"]:
            # A gate rejection does not touch memory. ACK only the live claim.
            if not self.finish(claim, JobOutcome.REJECTED):
                raise RuntimeError("EPISODE_WRITE_LEASE_LOST")
        return result

    def finish(self, claim: EpisodeClaim, outcome: JobOutcome) -> bool:
        """CAS completion; a stale lease cannot acknowledge another attempt.

        This fences ACK only. C.2B must fence the actual memory write.
        """
        if not isinstance(claim, EpisodeClaim) or not isinstance(outcome, JobOutcome):
            raise ValueError("EPISODE_WORK_OUTCOME_INVALID")
        if outcome in {JobOutcome.STORED, JobOutcome.DUPLICATE}:
            raise ValueError("EPISODE_SUCCESS_REQUIRES_ATOMIC_WRITE")
        db = self.database
        now = self._now()
        retry = outcome == JobOutcome.TRANSIENT_FAILURE and claim.attempts < self.max_attempts
        status = ("pending" if retry else "failed" if outcome in {
            JobOutcome.TRANSIENT_FAILURE, JobOutcome.PERMANENT_FAILURE} else "done")
        delay = min(900, 15 * 2 ** (claim.attempts - 1)) if retry else 0
        reason = ("EPISODE_ATTEMPTS_EXHAUSTED" if outcome == JobOutcome.TRANSIENT_FAILURE and not retry
                  else "EPISODE_" + outcome.value.upper())
        with trace_lock(db._lock):
            result = execute_atomic_statement(
                db.backend, db._conn,
                "UPDATE sensory_episode_jobs SET status=?,available_at=?,lease_token=NULL,lease_until=NULL,"
                "last_reason=? WHERE job_id=? AND status='claimed' AND lease_token=? "
                "AND attempts=? AND lease_until>?",
                (status, (now + timedelta(seconds=delay)).isoformat(), reason, str(claim.job_id),
                 str(claim.lease_token), claim.attempts, now.isoformat()),
            )
            return result.rowcount == 1

    def cancel(self, job_id: UUID) -> bool:
        """Cancel pending/claimed work; completed memory invalidation is C.2B."""
        db = self.database
        with trace_lock(db._lock):
            result = execute_atomic_statement(
                db.backend, db._conn,
                "UPDATE sensory_episode_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,"
                "last_reason='EPISODE_WORK_CANCELLED' WHERE job_id=? AND status IN ('pending','claimed')",
                (str(job_id),),
            )
            return result.rowcount == 1

    def invalidate_evidence(self, event_id: UUID) -> int:
        """Tombstone, cancel associated jobs and archive direct episodes atomically.

        Internal authorized invalidation only. Does not delete source content,
        cascade to unrelated derived memories or revoke prior disclosures.
        Refuse oversized fanout rather than silently leaving partial state.
        """
        if not isinstance(event_id, UUID):
            raise ValueError("EPISODE_INVALIDATION_ID_INVALID")
        key = str(event_id)
        db = self.database
        with trace_lock(db._lock), db._conn:
            jobs = db._conn.execute(
                "SELECT job_id FROM sensory_episode_job_evidence WHERE event_id=? LIMIT 129",
                (key,),
            ).fetchall()
            # Also cover direct D.2 episodes created before the queue existed.
            memories = db._conn.execute(
                "SELECT id FROM memory_records WHERE provenance_source='perception:episode:v1' "
                "AND archived_at IS NULL AND EXISTS (SELECT 1 FROM "
                "json_each(memory_records.metadata_json,'$.evidence_event_ids') WHERE value=?) LIMIT 129",
                (key,),
            ).fetchall()
            if len(jobs) > 128 or len(memories) > 128:
                raise RuntimeError("EPISODE_INVALIDATION_FANOUT_REVIEW_REQUIRED")
            now = self._now().isoformat()
            db._conn.execute(
                "INSERT INTO sensory_episode_invalidations(event_id,invalidated_at) VALUES (?,?) "
                "ON CONFLICT(event_id) DO NOTHING", (key, now),
            )
            db._conn.executemany(
                "UPDATE sensory_episode_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,"
                "last_reason='EPISODE_EVIDENCE_INVALIDATED' WHERE job_id=?",
                [(row[0],) for row in jobs],
            )
            db._conn.executemany(
                "UPDATE memory_records SET archived_at=?,updated_at=? WHERE id=? AND archived_at IS NULL",
                [(now, now, row[0]) for row in memories],
            )
            return len(memories)

    def state(self, job_id: UUID) -> dict | None:
        db = self.database
        with trace_lock(db._lock):
            row = db._conn.execute(
                "SELECT status,attempts,last_reason FROM sensory_episode_jobs WHERE job_id=?",
                (str(job_id),),
            ).fetchone()
            return dict(row) if row is not None else None
