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
    "CONTEXT_ENVELOPE_OVERFLOW", "REFLECTION_SKIPPED",
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
            "VALUES (?,?,'pending',?,?) ON CONFLICT(turn_id) DO NOTHING",
            (str(request.turn_id), str(input_row.id), now, now),
        ))

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
            "lease_token=?,lease_until=? WHERE turn_id=? AND attempts<? AND "
            "((status='pending' AND available_at<=?) OR (status='claimed' AND lease_until<=?))",
            (token, until, row["turn_id"], _MAX_ATTEMPTS, stamp, stamp),
        )
        return (row["turn_id"], row["input_id"], token, int(row["attempts"]) + 1) if won else None

    def finish(self, job, reason, *, retry=False):
        turn_id, _, token, attempts = job
        status = "pending" if retry and attempts < _MAX_ATTEMPTS else "failed" if retry else "done"
        delay = min(900, 15 * (2 ** (attempts - 1))) if status == "pending" else 0
        return bool(self._write(
            "UPDATE memory_reflection_jobs SET status=?,available_at=?,lease_token=NULL,lease_until=NULL,"
            "last_reason=? WHERE turn_id=? AND status='claimed' AND lease_token=?",
            (status, (self.clock.now() + timedelta(seconds=delay)).isoformat(), reason, turn_id, token),
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
        counts = {"pending": 0, "claimed": 0, "done": 0, "failed": 0}
        counts.update({row["status"]: int(row["total"]) for row in rows})
        return counts

    async def process_once(self):
        job = self.claim()
        if job is None:
            return False
        turn_id, input_id, _, _ = job
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
                          "durable_memory_reflection": True},
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
