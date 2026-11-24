"""RUN 01 durable reply queue repository (opt-in integration candidate).

References canonical inputs; no message bodies, provider output or media are
copied into the queue. The trusted Core must persist reply_enqueue_requested
and reply_outbound_id after authorizing ingress. This repository is not itself
an authorization grant. The queue shares the existing generation lease and the atomic text commit.
Activation requires a separately installed and verified schema.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from amora.latency_trace import trace_lock
from amora.storage.reply_job_sql import SQLITE_STATEMENTS
from .generation_lifecycle import InboundGeneration


class ReplyReason(StrEnum):
    PROVIDER_TEMPORARY = "PROVIDER_TEMPORARY"
    PROVIDER_BACKOFF = "PROVIDER_BACKOFF"
    GENERATION_TIMEOUT = "GENERATION_TIMEOUT"
    STORAGE_TEMPORARY = "STORAGE_TEMPORARY"
    CONFIGURATION_BLOCKED = "CONFIGURATION_BLOCKED"
    CONTEXT_BLOCKED = "CONTEXT_BLOCKED"
    AUTHORIZATION_REVOKED = "AUTHORIZATION_REVOKED"
    SOURCE_INVALIDATED = "SOURCE_INVALIDATED"


@dataclass(frozen=True, slots=True)
class ReplyClaim:
    turn_id: str
    input_id: str
    session_id: str
    token: str
    attempts: int


class _LeaseConflict(RuntimeError):
    """Rollback both sides of a failed paired lease transition."""


class ReplyJobRepository:
    """Candidate using the existing database/clock transaction boundary.

    No automatic migration, worker, polling task or provider access.
    PostgreSQL requires explicitly injected, reviewed candidate SQL mappings.
    No production registry or environment selection is changed here.
    All operations are synchronous database-only; do not await inside a tx.
    """

    def __init__(self, database, clock, *, lease_seconds: int = 180,
                 max_running: int = 1):
        if database.backend.capabilities.name not in {"sqlite", "postgres-candidate"}:
            raise ValueError("REPLY_JOB_BACKEND_NOT_CERTIFIED")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 900:
            raise ValueError("REPLY_JOB_LEASE_INVALID")
        if type(max_running) is not int or not 1 <= max_running <= 8:
            raise ValueError("REPLY_JOB_CONCURRENCY_INVALID")
        self.database = database
        self._postgres = database.backend.capabilities.name == "postgres-candidate"
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.max_running = max_running
        self.generations = InboundGeneration(database, clock)

    def _sql(self, operation: str) -> str:
        sql = SQLITE_STATEMENTS[operation]  # reject unknown operations
        return "PG_REPLY_JOB_" + operation if self._postgres else sql

    def _now(self):
        return self.clock.now().astimezone(timezone.utc)

    def _write(self, sql, args=()):
        db = self.database
        with trace_lock(db._lock), db._conn:
            return db._conn.execute(sql, args).rowcount

    def enqueue(self, input_id: str) -> bool:
        # No raw content in parameters; invalid, old or unauthorized inputs
        # cannot create a job even if their IDs are supplied.
        return self._write(
            self._sql("ENQUEUE"),
            (self._now().isoformat(), str(input_id)),
        ) == 1

    def reconcile_missing(self, *, limit: int = 32) -> int:
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("REPLY_JOB_RECONCILE_LIMIT_INVALID")
        return self._write(
            self._sql("RECONCILE"), (self._now().isoformat(), limit),
        )

    def cancel_invalidated(self) -> int:
        # The existing source tombstone is the authority. Do not retract sent
        # replies; cancelling their transport/source remains the adapter's job.
        return self._write(self._sql("CANCEL_INVALIDATED"))

    def claim(self) -> ReplyClaim | None:
        db = self.database
        try:
            with trace_lock(db._lock), db._conn:
                # PostgreSQL needs a transaction-scoped lock for its global
                # count/claim predicate. SQLite already uses BEGIN IMMEDIATE.
                if self._postgres:
                    db._conn.execute("PG_REPLY_JOB_CLAIM_LOCK")
                now = self._now()
                stamp = now.isoformat()
                count = db._conn.execute(
                    self._sql("RUNNING_COUNT"), (stamp,),
                ).fetchone()[0]
                if count >= self.max_running:
                    return None
                row = db._conn.execute(
                    self._sql("SELECT_DUE"), (stamp, stamp, stamp, stamp),
                ).fetchone()
                if row is None:
                    return None
                token = str(uuid4())
                until = (now + timedelta(seconds=self.lease_seconds)).isoformat()
                won = db._conn.execute(
                    self._sql("CLAIM"),
                    (token, until, row["turn_id"], row["attempts"], stamp, stamp),
                ).rowcount
                if won != 1:
                    return None
                # The queue and the *existing* generation/outbound fence use
                # exactly the same token and deadline, in one transaction.
                if not self.generations.claim(
                    row["turn_id"], row["session_id"], row["native_message_hash"],
                    row["transport_revision"], token, seconds=self.lease_seconds,
                    now=now,
                ):
                    raise _LeaseConflict()
                return ReplyClaim(row["turn_id"], row["input_id"], row["session_id"],
                                  token, int(row["attempts"]) + 1)
        except _LeaseConflict:
            return None

    def _paired_write(self, claim, operation, args, *, renew_now=None, completed=False):
        db = self.database
        try:
            with trace_lock(db._lock), db._conn:
                if db._conn.execute(self._sql(operation), args).rowcount != 1:
                    return False
                if renew_now is not None:
                    won = self.generations.renew(
                        claim.turn_id, claim.token, seconds=self.lease_seconds,
                        now=renew_now,
                    )
                else:
                    won = self.generations.release(
                        claim.turn_id, claim.token, completed=completed,
                    )
                if not won:
                    raise _LeaseConflict()
                return True
        except _LeaseConflict:
            return False

    def renew(self, claim: ReplyClaim) -> bool:
        now = self._now()
        return self._paired_write(
            claim, "RENEW",
            ((now + timedelta(seconds=self.lease_seconds)).isoformat(),
             claim.turn_id, claim.token, now.isoformat()),
            renew_now=now,
        )

    def defer(self, claim: ReplyClaim, reason: ReplyReason, *, delay_seconds: int) -> bool:
        reason = ReplyReason(reason)
        if reason not in {ReplyReason.PROVIDER_TEMPORARY, ReplyReason.PROVIDER_BACKOFF,
                          ReplyReason.GENERATION_TIMEOUT, ReplyReason.STORAGE_TEMPORARY}:
            raise ValueError("REPLY_JOB_RETRY_REASON_INVALID")
        if type(delay_seconds) is not int or not 1 <= delay_seconds <= 900:
            raise ValueError("REPLY_JOB_DELAY_INVALID")
        now = self._now()
        return self._paired_write(
            claim, "DEFER",
            ((now + timedelta(seconds=delay_seconds)).isoformat(), reason.value,
             claim.turn_id, claim.token, now.isoformat()),
        )

    def block(self, claim: ReplyClaim, reason: ReplyReason) -> bool:
        reason = ReplyReason(reason)
        if reason not in {ReplyReason.CONFIGURATION_BLOCKED, ReplyReason.CONTEXT_BLOCKED,
                          ReplyReason.AUTHORIZATION_REVOKED}:
            raise ValueError("REPLY_JOB_BLOCK_REASON_INVALID")
        return self._paired_write(
            claim, "BLOCK",
            (reason.value, claim.turn_id, claim.token, self._now().isoformat()),
            completed=reason is ReplyReason.AUTHORIZATION_REVOKED,
        )

    def resume_blocked(self, turn_id: str) -> bool:
        # Internal operation only; real ingress/dispatcher must reauthorize.
        # Revoked authorization never resumes through a configuration fix.
        return self._write(
            self._sql("RESUME"), (self._now().isoformat(), str(turn_id)),
        ) == 1

    def mark_ready(self, claim: ReplyClaim, outbound_id: str) -> bool:
        """Observe durable outbox; does NOT itself write or deliver a reply.

        Full integration must stage and publish with this same generation token,
        and reconcile commit-unknown/prepared response crashes.
        """
        oid = str(UUID(str(outbound_id)))
        return self._paired_write(
            claim, "MARK_READY",
            (oid, claim.turn_id, claim.token, self._now().isoformat(), oid),
            completed=True,
        )

    def observe_sent(self, turn_id: str) -> bool:
        # Never equate generated/queued text with successful transport ACK.
        return self._write(self._sql("OBSERVE_SENT"), (str(turn_id),),
        ) == 1

    def enqueue_turn(self, turn_id: str) -> bool:
        return self._write(self._sql("ENQUEUE_TURN"),
            (self._now().isoformat(), str(turn_id))) == 1

    def load(self, claim: ReplyClaim):
        with trace_lock(self.database._lock):
            row = self.database._conn.execute(self._sql("LOAD"),
                (claim.turn_id, claim.token, self._now().isoformat())).fetchone()
        return dict(row) if row else None

    def counts(self):
        with trace_lock(self.database._lock):
            rows = self.database._conn.execute(self._sql("COUNTS")).fetchall()
        return {row["status"]: int(row["count"]) for row in rows}

    def reconcile_outbound(self):
        return self._write(self._sql("RECONCILE_OUTBOUND"))

    def finalize(self, claim, store, message, decision, outbound, reasons, records):
        """Commit history, original outbox/audit and ready state together.

        The caller supplies validated, deterministic history values, never
        SQL. No network await, nested SQLite transaction or provider call.
        Existing prepared checkpoint survives any failed transaction.
        """
        db = self.database
        oid = str(outbound.id)
        try:
            with trace_lock(db._lock), db._conn:
                if db._conn.execute(self._sql("FINALIZE_LOCK"),
                        (claim.turn_id, claim.token, self._now().isoformat())).fetchone() is None:
                    return False
                for segment_values, message_values in records:
                    if db._conn.execute(self._sql("SEGMENT_INSERT"), segment_values).rowcount != 1:
                        raise ValueError("DURABLE_REPLY_SEGMENT_CONFLICT")
                    if db._conn.execute(self._sql("HISTORY_INSERT"), message_values).rowcount != 1:
                        raise ValueError("DURABLE_REPLY_HISTORY_CONFLICT")
                persisted = store.persist_prepared_reply(
                    message, decision, outbound, reasons,
                    lease=(claim.turn_id, claim.token),
                    native_hash=outbound.metadata["durable_native_hash"],
                )
                if (persisted.id != outbound.id or persisted.text != outbound.text
                        or persisted.source_message_id != outbound.source_message_id
                        or persisted.conversation_id != outbound.conversation_id):
                    raise ValueError("DURABLE_REPLY_OUTBOUND_CONFLICT")
                if db._conn.execute(self._sql("MARK_READY"),
                        (oid, claim.turn_id, claim.token, self._now().isoformat(), oid)).rowcount != 1:
                    raise _LeaseConflict()
                if not self.generations.release(claim.turn_id, claim.token, completed=True):
                    raise _LeaseConflict()
                return True
        except _LeaseConflict:
            return False

    def state(self, turn_id: str):
        with trace_lock(self.database._lock):
            row = self.database._conn.execute(
                self._sql("STATE"), (str(turn_id),),
            ).fetchone()
        return dict(row) if row else None
