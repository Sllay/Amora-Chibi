"""Durable, bounded reflection processing for accepted platform messages."""
from __future__ import annotations

import asyncio
import logging
from datetime import timedelta
from uuid import UUID, uuid4

from amora.cognition import CognitiveRequest, JournalStage
from amora.events import PrivacyScope
from amora.storage.backend import execute_atomic_statement

_LOG = logging.getLogger("amora.messaging.reflection")
_MAX_ATTEMPTS = 5
_LEASE_SECONDS = 180
_TIMEOUT_SECONDS = 60
_RETRY_REASONS = {
    "REFLECTION_PROVIDER_FAILURE", "NO_STRUCTURED_REFLECTION_PROVIDER",
    "CONTEXT_ENVELOPE_OVERFLOW", "REFLECTION_SKIPPED", "REFLECTION_LEASE_LOST",
}


class ReflectionQueue:
    """Jobs reference existing observed inputs; no duplicate plaintext payload."""

    def __init__(self, database, cognition, clock):
        self.database = database
        self.cognition = cognition
        self.clock = clock
        self._task = None

    def _write(self, sql, args):
        db = self.database
        with db._lock:
            return execute_atomic_statement(db.backend, db._conn, sql, args).rowcount

    def enqueue(self, request: CognitiveRequest) -> bool:
        if (not request.remote_processing_allowed
                or request.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}
                or request.metadata.get("test_mode")
                or request.metadata.get("memory_consolidation") is False
                or request.metadata.get("surface") != "platform_messaging"):
            return False
        input_row = self.cognition.store.get_message_by_dedupe(
            f"cognitive-input:{request.turn_id}"
        )
        if input_row is None:
            raise RuntimeError("REFLECTION_INPUT_NOT_PERSISTED")
        now = self.clock.now().isoformat()
        return bool(self._write(
            "INSERT INTO memory_reflection_jobs(turn_id,input_id,status,created_at,available_at) "
            "SELECT ?,?,'pending',?,? WHERE NOT EXISTS ("
            "SELECT 1 FROM memory_reflection_invalidations x WHERE x.session_id=? "
            "AND x.native_message_hash=? AND x.invalidated_through>=?) "
            "ON CONFLICT(turn_id) DO NOTHING",
            (str(request.turn_id), str(input_row.id), now, now, input_row.session_id,
             input_row.metadata.get("native_message_id_hash", ""),
             int(input_row.metadata.get("transport_revision", 1))),
        ))

    def reconcile_missing(self):
        """Recover the crash gap between persisted input and job insertion.

        Only new inputs carrying the trusted enqueue marker participate.
        Historical data never becomes a remote job merely by upgrading.
        """
        now = self.clock.now().isoformat()
        return self._write(
            """INSERT INTO memory_reflection_jobs(turn_id,input_id,status,created_at,available_at)
               SELECT m.turn_id,m.id,'pending',m.started_at,?
               FROM conversation_messages m
               WHERE m.dedupe_key='cognitive-input:' || m.turn_id
                 AND m.remote_processing_allowed=1 AND m.privacy_scope NOT IN ('AUTH','SECRET')
                 AND json_extract(m.metadata_json,'$.surface')='platform_messaging'
                 AND json_extract(m.metadata_json,'$.reflection_enqueue_requested')=1
                 AND COALESCE(json_extract(m.metadata_json,'$.test_mode'),0)=0
                 AND COALESCE(json_extract(m.metadata_json,'$.memory_consolidation'),1)=1
                 AND NOT EXISTS (SELECT 1 FROM memory_reflection_jobs j WHERE j.turn_id=m.turn_id)
                 AND NOT EXISTS (SELECT 1 FROM memory_reflection_invalidations x
                     WHERE x.session_id=m.session_id
                     AND x.native_message_hash=json_extract(m.metadata_json,'$.native_message_id_hash')
                     AND x.invalidated_through>=COALESCE(json_extract(m.metadata_json,'$.transport_revision'),1))
               ORDER BY m.started_at,m.turn_id LIMIT 32
               ON CONFLICT(turn_id) DO NOTHING""",
            (now,),
        )

    def source_invalidated(self, session_id, native_message_hash, revision):
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT 1 FROM memory_reflection_invalidations WHERE session_id=? "
                "AND native_message_hash=? AND invalidated_through>=?",
                (session_id, native_message_hash, revision),
            ).fetchone()
        return row is not None

    def invalidate_source(self, session_id, native_message_hash, *, through_revision):
        """Cancel stale revisions even if an invalidation precedes delivery.

        Preserve audit rows and archive only memories derived from the stale turn.
        A newer revision has a new turn ID and is independently eligible.
        """
        db = self.database
        now = self.clock.now().isoformat()
        with db._lock, db._conn:
            db._conn.execute(
                "INSERT INTO memory_reflection_invalidations(session_id,native_message_hash,invalidated_through) "
                "VALUES (?,?,?) ON CONFLICT(session_id,native_message_hash) DO UPDATE SET "
                "invalidated_through=MAX(invalidated_through,excluded.invalidated_through)",
                (session_id, native_message_hash, through_revision),
            )
            # The persisted input is the authority for the native source and revision.
            stale = """
                SELECT m.turn_id FROM conversation_messages m
                JOIN memory_reflection_invalidations x ON x.session_id=m.session_id
                AND x.native_message_hash=json_extract(m.metadata_json,'$.native_message_id_hash')
                WHERE x.session_id=? AND x.native_message_hash=?
                AND m.dedupe_key='cognitive-input:' || m.turn_id
                AND COALESCE(json_extract(m.metadata_json,'$.transport_revision'),1)<=x.invalidated_through
            """
            cursor = db._conn.execute(
                "UPDATE memory_reflection_jobs SET status='cancelled',lease_token=NULL,lease_until=NULL,"
                "last_reason='REFLECTION_SOURCE_INVALIDATED' WHERE status<>'cancelled' AND turn_id IN ("+stale+")",
                (session_id, native_message_hash),
            )
            db._conn.execute(
                "UPDATE memory_records SET archived_at=?,updated_at=? WHERE archived_at IS NULL "
                "AND provenance_source LIKE 'cognition:%' AND json_extract(metadata_json,'$.turn_id') IN ("+stale+")",
                (now, now, session_id, native_message_hash),
            )
            return cursor.rowcount

    def retry_failed(self, turn_id):
        """An operator can rearm exactly one failed task, never a cancelled task."""
        return bool(self._write(
            "UPDATE memory_reflection_jobs SET status='pending',attempts=0,available_at=?,"
            "lease_token=NULL,lease_until=NULL,last_reason='REFLECTION_OPERATOR_RETRY' "
            "WHERE turn_id=? AND status='failed'",
            (self.clock.now().isoformat(), str(turn_id)),
        ))

    def recover_historical_turn(self, turn_id):
        """Explicit recovery of one observed, eligible private platform turn."""
        item = self.cognition.store.get_message_by_dedupe(f"cognitive-input:{turn_id}")
        if (item is None or not item.raw_text or not item.speaker_id
                or not item.session_id or item.privacy_scope != PrivacyScope.PERSONAL
                or not item.remote_processing_allowed
                or item.metadata.get("surface") != "platform_messaging"
                or item.metadata.get("channel_type") != "dm"
                or item.metadata.get("test_mode")
                or item.metadata.get("memory_consolidation") is False
                or self.cognition.compiler.identities.get(item.speaker_id) is None):
            return False
        # Never duplicate an existing completed consolidation; old memories can
        # be explicitly reviewed for reuse through the separate authorization path.
        if any(entry.stage == JournalStage.PROPOSALS_GATED
               for entry in self.cognition.store.journal_for_turn(turn_id)):
            return False
        request = CognitiveRequest(
            turn_id=UUID(str(turn_id)), text=item.raw_text, speaker_id=item.speaker_id,
            target_id=item.addressee_id, session_id=item.session_id,
            privacy_scope=item.privacy_scope, source_event_id=item.source_event_id,
            remote_processing_allowed=item.remote_processing_allowed,
            metadata=item.metadata,
        )
        return self.enqueue(request)

    def historical_audit(self):
        """Counts and bounded identifiers only; no conversation or memory contents."""
        with self.database._lock:
            conn = self.database._conn
            rows = conn.execute(
                "SELECT m.turn_id FROM conversation_messages m "
                "WHERE m.dedupe_key='cognitive-input:' || m.turn_id "
                "AND m.privacy_scope='PERSONAL' AND m.remote_processing_allowed=1 "
                "AND m.speaker_id IS NOT NULL AND m.session_id IS NOT NULL "
                "AND json_extract(m.metadata_json,'$.surface')='platform_messaging' "
                "AND json_extract(m.metadata_json,'$.channel_type')='dm' "
                "AND COALESCE(json_extract(m.metadata_json,'$.test_mode'),0)=0 "
                "AND COALESCE(json_extract(m.metadata_json,'$.memory_consolidation'),1)=1 "
                "AND NOT EXISTS (SELECT 1 FROM memory_reflection_jobs j WHERE j.turn_id=m.turn_id) "
                "AND NOT EXISTS (SELECT 1 FROM cognitive_journal j WHERE j.turn_id=m.turn_id AND j.stage='proposals_gated') "
                "AND NOT EXISTS (SELECT 1 FROM memory_reflection_invalidations x WHERE x.session_id=m.session_id "
                "AND x.native_message_hash=json_extract(m.metadata_json,'$.native_message_id_hash') "
                "AND x.invalidated_through>=COALESCE(json_extract(m.metadata_json,'$.transport_revision'),1)) "
                "ORDER BY m.started_at DESC LIMIT 100"
            ).fetchall()
        return {"unconsolidated_turn_candidates": [row["turn_id"] for row in rows],
                "local_only_reflections": self.cognition.compiler.memory.store.local_only_reflection_audit(),
                "queue": self.counts()}

    def failed_jobs(self, limit=50):
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT turn_id,attempts,last_reason FROM memory_reflection_jobs "
                "WHERE status='failed' ORDER BY created_at DESC LIMIT ?",
                (max(1, min(100, int(limit))),),
            ).fetchall()
        return [dict(row) for row in rows]

    def claim(self):
        now = self.clock.now()
        stamp = now.isoformat()
        db = self.database
        # A crashed final attempt is terminal instead of remaining leased forever.
        self._write(
            "UPDATE memory_reflection_jobs SET status='failed',lease_token=NULL,lease_until=NULL,"
            "last_reason='REFLECTION_ATTEMPTS_EXHAUSTED' WHERE status='claimed' AND lease_until<=? AND attempts>=?",
            (stamp, _MAX_ATTEMPTS),
        )
        with db._lock:
            row = db._conn.execute(
                "SELECT turn_id,input_id,attempts FROM memory_reflection_jobs "
                "WHERE attempts<? AND ((status='pending' AND available_at<=?) "
                "OR (status='claimed' AND lease_until<=?)) "
                "ORDER BY available_at,created_at,turn_id LIMIT 1",
                (_MAX_ATTEMPTS, stamp, stamp),
            ).fetchone()
        if row is None:
            return None
        token = str(uuid4())
        until = (now + timedelta(seconds=_LEASE_SECONDS)).isoformat()
        won = self._write(
            "UPDATE memory_reflection_jobs SET status='claimed',attempts=attempts+1,"
            "lease_token=?,lease_until=? WHERE turn_id=? AND attempts=? AND attempts<? AND "
            "((status='pending' AND available_at<=?) OR (status='claimed' AND lease_until<=?))",
            (token, until, row["turn_id"], row["attempts"], _MAX_ATTEMPTS, stamp, stamp),
        )
        return (row["turn_id"], row["input_id"], token, int(row["attempts"]) + 1) if won else None

    def finish(self, job, reason, *, retry=False):
        turn_id, _, token, attempts = job
        status = "pending" if retry and attempts < _MAX_ATTEMPTS else "failed" if retry else "done"
        delay = min(900, 15 * (2 ** (attempts - 1))) if status == "pending" else 0
        return bool(self._write(
            "UPDATE memory_reflection_jobs SET status=?,available_at=?,lease_token=NULL,lease_until=NULL,"
            "last_reason=? WHERE turn_id=? AND status='claimed' AND lease_token=? AND lease_until>?",
            (status, (self.clock.now() + timedelta(seconds=delay)).isoformat(), reason, turn_id, token,
             self.clock.now().isoformat()),
        ))

    def state(self, turn_id):
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT status,attempts,last_reason FROM memory_reflection_jobs WHERE turn_id=?",
                (str(turn_id),),
            ).fetchone()
        return dict(row) if row else None

    def counts(self):
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT status, COUNT(*) AS total FROM memory_reflection_jobs GROUP BY status"
            ).fetchall()
        counts = {"pending": 0, "claimed": 0, "done": 0, "failed": 0, "cancelled": 0}
        counts.update({row["status"]: int(row["total"]) for row in rows})
        return counts

    async def process_once(self):
        self.reconcile_missing()
        job = self.claim()
        if job is None:
            return False
        turn_id, input_id, lease_token, _ = job
        try:
            item = self.cognition.store.get_message_by_dedupe(f"cognitive-input:{turn_id}")
            if (item is None or str(item.id) != input_id or not item.raw_text
                    or not item.speaker_id or not item.remote_processing_allowed
                    or item.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}
                    or item.metadata.get("test_mode")
                    or item.metadata.get("memory_consolidation") is False):
                self.finish(job, "REFLECTION_INPUT_INELIGIBLE")
                return True
            # A crash after the completed gate journal but before queue ACK
            # must not rerun a completed reflection.
            completed = next((entry for entry in reversed(self.cognition.store.journal_for_turn(turn_id))
                              if entry.stage == JournalStage.PROPOSALS_GATED), None)
            if completed is not None:
                self.finish(job, "MEMORY_REFLECTION_ACCEPTED" if completed.payload.get("accepted_memory_ids")
                            else "MEMORY_REFLECTION_NO_ACCEPTED_MEMORY")
                return True
            request = CognitiveRequest(
                turn_id=UUID(turn_id), text=item.raw_text, speaker_id=item.speaker_id,
                target_id=item.addressee_id, session_id=item.session_id,
                source_event_id=item.source_event_id, privacy_scope=item.privacy_scope,
                remote_processing_allowed=item.remote_processing_allowed,
                metadata={**item.metadata, "cognitive_mode": "reflection",
                          "durable_memory_reflection": True,
                          "reflection_lease_token": lease_token},
            )
            bundle = await asyncio.wait_for(self.cognition.reflect(request), timeout=_TIMEOUT_SECONDS)
            retry = next((reason for reason in bundle.reason_codes if reason in _RETRY_REASONS), None)
            if retry:
                self.finish(job, retry, retry=True)
            else:
                self.finish(job, "MEMORY_REFLECTION_ACCEPTED" if bundle.accepted_memory_ids
                            else "MEMORY_REFLECTION_NO_ACCEPTED_MEMORY")
        except asyncio.CancelledError:
            self.finish(job, "REFLECTION_SHUTDOWN", retry=True)
            raise
        except TimeoutError:
            self.finish(job, "MEMORY_REFLECTION_TIMEOUT", retry=True)
        except Exception as exc:
            self.finish(job, "MEMORY_REFLECTION_FAILED", retry=True)
            _LOG.warning("reflection_job_failed type=%s", type(exc).__name__)
        return True

    def start(self):
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="amora-reflection-queue")

    async def stop(self):
        if self._task is not None:
            task, self._task = self._task, None
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _run(self):
        while True:
            try:
                worked = await self.process_once()
            except Exception as exc:
                _LOG.warning("reflection_queue_failed type=%s", type(exc).__name__)
                worked = False
            await asyncio.sleep(0.1 if worked else 2.0)
