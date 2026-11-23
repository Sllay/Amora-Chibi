"""RUN 01 isolated durable reply queue. Not wired into serving Core.

References canonical inputs; no message bodies, provider output or media are
copied into the queue. The trusted Core must persist reply_enqueue_requested
and reply_outbound_id after authorizing ingress. This repository is not itself
an authorization grant. PostgreSQL reviewed SQL and the shared generation/
outbound fence must be integrated before enabling the dispatcher.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta, timezone
from enum import StrEnum
from uuid import UUID, uuid4

from amora.latency_trace import trace_lock


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


# Only Core-authored input metadata is eligible. A schema candidate cannot
# turn historical conversation rows into new remote work.
_ELIGIBLE = """
    m.dedupe_key >= 'cognitive-input:'
    AND m.dedupe_key < 'cognitive-input;'
    AND m.dedupe_key='cognitive-input:' || m.turn_id
    AND m.remote_processing_allowed=1 AND m.privacy_scope='PERSONAL'
    AND json_extract(m.metadata_json,'$.surface')='platform_messaging'
    AND json_extract(m.metadata_json,'$.platform')='discord'
    AND json_extract(m.metadata_json,'$.channel_type')='dm'
    AND json_extract(m.metadata_json,'$.reply_enqueue_requested')=1
    AND COALESCE(json_extract(m.metadata_json,'$.test_mode'),0)=0
    AND m.session_id IS NOT NULL AND m.session_id<>''
    AND length(json_extract(m.metadata_json,'$.native_message_id_hash'))=20
    AND json_extract(m.metadata_json,'$.native_message_id_hash') NOT GLOB '*[^0-9a-f]*'
    AND typeof(json_extract(m.metadata_json,'$.transport_revision'))='integer'
    AND json_extract(m.metadata_json,'$.transport_revision')>=1
    AND length(json_extract(m.metadata_json,'$.reply_outbound_id'))=36
    AND NOT EXISTS (
        SELECT 1 FROM memory_reflection_invalidations x
        WHERE x.session_id=m.session_id
          AND x.native_message_hash=json_extract(m.metadata_json,'$.native_message_id_hash')
          AND x.invalidated_through>=json_extract(m.metadata_json,'$.transport_revision')
    )
"""
_INSERT = """
    INSERT INTO messaging_reply_jobs(
        turn_id,input_id,session_id,native_message_hash,transport_revision,
        status,created_at,available_at)
    SELECT m.turn_id,m.id,m.session_id,
        json_extract(m.metadata_json,'$.native_message_id_hash'),
        json_extract(m.metadata_json,'$.transport_revision'),
        'pending',m.started_at,?
    FROM conversation_messages m
    WHERE
""" + _ELIGIBLE

_VALID_SOURCE = """
    NOT EXISTS (
        SELECT 1 FROM memory_reflection_invalidations x
        WHERE x.session_id=messaging_reply_jobs.session_id
          AND x.native_message_hash=messaging_reply_jobs.native_message_hash
          AND x.invalidated_through>=messaging_reply_jobs.transport_revision
    )
    AND EXISTS (
        SELECT 1 FROM conversation_messages m
        WHERE m.id=messaging_reply_jobs.input_id
          AND m.turn_id=messaging_reply_jobs.turn_id
          AND m.session_id=messaging_reply_jobs.session_id
          AND m.remote_processing_allowed=1
          AND m.privacy_scope='PERSONAL'
    )
"""
_OWNED = "turn_id=? AND status='running' AND lease_token=? AND lease_until>?"


class ReplyJobRepository:
    """SQLite candidate using the existing database/clock transaction boundary.

    No automatic migration, worker, polling task or provider access.
    Explicitly reject other backends until their audited mappings are added.
    All operations are synchronous database-only; do not await inside a tx.
    """

    def __init__(self, database, clock, *, lease_seconds: int = 180,
                 max_running: int = 1):
        if database.backend.capabilities.name != "sqlite":
            raise ValueError("REPLY_JOB_BACKEND_NOT_CERTIFIED")
        if type(lease_seconds) is not int or not 5 <= lease_seconds <= 900:
            raise ValueError("REPLY_JOB_LEASE_INVALID")
        if type(max_running) is not int or not 1 <= max_running <= 8:
            raise ValueError("REPLY_JOB_CONCURRENCY_INVALID")
        self.database = database
        self.clock = clock
        self.lease_seconds = lease_seconds
        self.max_running = max_running

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
            _INSERT + " AND m.id=? ON CONFLICT(turn_id) DO NOTHING",
            (self._now().isoformat(), str(input_id)),
        ) == 1

    def reconcile_missing(self, *, limit: int = 32) -> int:
        if type(limit) is not int or not 1 <= limit <= 128:
            raise ValueError("REPLY_JOB_RECONCILE_LIMIT_INVALID")
        return self._write(
            _INSERT + """
            AND NOT EXISTS (SELECT 1 FROM messaging_reply_jobs j WHERE j.turn_id=m.turn_id)
            ORDER BY m.started_at,m.turn_id LIMIT ?
            ON CONFLICT(turn_id) DO NOTHING
            """, (self._now().isoformat(), limit),
        )

    def cancel_invalidated(self) -> int:
        # The existing source tombstone is the authority. Do not retract sent
        # replies; cancelling their transport/source remains the adapter's job.
        return self._write("""
            UPDATE messaging_reply_jobs
            SET status='cancelled',lease_token=NULL,lease_until=NULL,
                last_reason='SOURCE_INVALIDATED'
            WHERE turn_id IN (
                SELECT turn_id FROM messaging_reply_jobs
                WHERE status IN ('pending','running','retry_wait','ready','blocked')
                  AND NOT (""" + _VALID_SOURCE + """)
                ORDER BY created_at,turn_id LIMIT 128
            )""")

    def claim(self) -> ReplyClaim | None:
        db = self.database
        with trace_lock(db._lock), db._conn:
            now = self._now()
            stamp = now.isoformat()
            # BEGIN IMMEDIATE in the existing SQLite backend serializes
            # capacity check and claim across connections.
            count = db._conn.execute(
                "SELECT COUNT(*) FROM messaging_reply_jobs "
                "WHERE status='running' AND lease_until>?", (stamp,),
            ).fetchone()[0]
            if count >= self.max_running:
                return None
            row = db._conn.execute("""
                SELECT turn_id,input_id,session_id,attempts
                FROM messaging_reply_jobs
                WHERE ((status IN ('pending','retry_wait') AND available_at<=?)
                    OR (status='running' AND lease_until<=?))
                  AND """ + _VALID_SOURCE + """
                  AND NOT EXISTS (
                    SELECT 1 FROM messaging_reply_jobs earlier
                    WHERE earlier.session_id=messaging_reply_jobs.session_id
                      AND earlier.status IN ('pending','retry_wait','running','ready')
                      AND (earlier.created_at<messaging_reply_jobs.created_at OR
                          (earlier.created_at=messaging_reply_jobs.created_at
                           AND earlier.turn_id<messaging_reply_jobs.turn_id))
                  )
                ORDER BY available_at,created_at,turn_id LIMIT 1
            """, (stamp, stamp)).fetchone()
            if row is None:
                return None
            token = str(uuid4())
            until = (now + timedelta(seconds=self.lease_seconds)).isoformat()
            won = db._conn.execute("""
                UPDATE messaging_reply_jobs SET status='running',attempts=attempts+1,
                    lease_token=?,lease_until=?
                WHERE turn_id=? AND attempts=?
                    AND ((status IN ('pending','retry_wait') AND available_at<=?)
                        OR (status='running' AND lease_until<=?))
                    AND """ + _VALID_SOURCE,
                (token, until, row["turn_id"], row["attempts"], stamp, stamp),
            ).rowcount
            if won != 1:
                return None
            return ReplyClaim(row["turn_id"], row["input_id"], row["session_id"],
                              token, int(row["attempts"]) + 1)

    def renew(self, claim: ReplyClaim) -> bool:
        now = self._now()
        return self._write(
            "UPDATE messaging_reply_jobs SET lease_until=? WHERE " + _OWNED
            + " AND " + _VALID_SOURCE,
            ((now + timedelta(seconds=self.lease_seconds)).isoformat(),
             claim.turn_id, claim.token, now.isoformat()),
        ) == 1

    def defer(self, claim: ReplyClaim, reason: ReplyReason, *, delay_seconds: int) -> bool:
        reason = ReplyReason(reason)
        if reason not in {ReplyReason.PROVIDER_TEMPORARY, ReplyReason.PROVIDER_BACKOFF,
                          ReplyReason.GENERATION_TIMEOUT, ReplyReason.STORAGE_TEMPORARY}:
            raise ValueError("REPLY_JOB_RETRY_REASON_INVALID")
        if type(delay_seconds) is not int or not 1 <= delay_seconds <= 900:
            raise ValueError("REPLY_JOB_DELAY_INVALID")
        now = self._now()
        return self._write(
            "UPDATE messaging_reply_jobs SET status='retry_wait',available_at=?,"
            "last_reason=?,lease_token=NULL,lease_until=NULL WHERE " + _OWNED
            + " AND " + _VALID_SOURCE,
            ((now + timedelta(seconds=delay_seconds)).isoformat(), reason.value,
             claim.turn_id, claim.token, now.isoformat()),
        ) == 1

    def block(self, claim: ReplyClaim, reason: ReplyReason) -> bool:
        reason = ReplyReason(reason)
        if reason not in {ReplyReason.CONFIGURATION_BLOCKED, ReplyReason.CONTEXT_BLOCKED,
                          ReplyReason.AUTHORIZATION_REVOKED}:
            raise ValueError("REPLY_JOB_BLOCK_REASON_INVALID")
        return self._write(
            "UPDATE messaging_reply_jobs SET status='blocked',last_reason=?,"
            "lease_token=NULL,lease_until=NULL WHERE " + _OWNED,
            (reason.value, claim.turn_id, claim.token, self._now().isoformat()),
        ) == 1

    def resume_blocked(self, turn_id: str) -> bool:
        # Internal operation only; real ingress/dispatcher must reauthorize.
        # Revoked authorization never resumes through a configuration fix.
        return self._write(
            "UPDATE messaging_reply_jobs SET status='pending',available_at=?,"
            "last_reason=NULL WHERE turn_id=? AND status='blocked' "
            "AND last_reason IN ('CONFIGURATION_BLOCKED','CONTEXT_BLOCKED') AND "
            + _VALID_SOURCE, (self._now().isoformat(), str(turn_id)),
        ) == 1

    def mark_ready(self, claim: ReplyClaim, outbound_id: str) -> bool:
        """Observe durable outbox; does NOT itself write or deliver a reply.

        Full integration must fence the original outbox write with the same
        job ownership, and reconcile commit-unknown/prepared response crashes.
        """
        oid = str(UUID(str(outbound_id)))
        return self._write(
            "UPDATE messaging_reply_jobs SET status='ready',outbound_id=?,"
            "lease_token=NULL,lease_until=NULL,last_reason=NULL WHERE " + _OWNED
            + " AND " + _VALID_SOURCE + """
            AND EXISTS (
                SELECT 1 FROM conversation_messages m
                JOIN messaging_outbound_queue o
                  ON o.id=json_extract(m.metadata_json,'$.reply_outbound_id')
                WHERE m.id=messaging_reply_jobs.input_id AND o.id=?
                  AND o.status IN ('queued','claimed','sent')
            )""", (oid, claim.turn_id, claim.token, self._now().isoformat(), oid),
        ) == 1

    def observe_sent(self, turn_id: str) -> bool:
        # Never equate generated/queued text with successful transport ACK.
        return self._write("""
            UPDATE messaging_reply_jobs SET status='sent'
            WHERE turn_id=? AND status='ready' AND EXISTS (
                SELECT 1 FROM messaging_outbound_queue o
                WHERE o.id=messaging_reply_jobs.outbound_id AND o.status='sent'
            )""", (str(turn_id),),
        ) == 1

    def state(self, turn_id: str):
        with trace_lock(self.database._lock):
            row = self.database._conn.execute(
                "SELECT status,attempts,available_at,lease_until,last_reason,outbound_id "
                "FROM messaging_reply_jobs WHERE turn_id=?", (str(turn_id),),
            ).fetchone()
        return dict(row) if row else None
