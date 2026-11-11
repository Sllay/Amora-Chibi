"""Recoverable, bounded reflection processing, outside the Discord response path.

The persisted payload is Core-private conversational data, never an audit or
public projection. A single SQL UPDATE ... RETURNING claims a lease atomically
across Core replicas. Failed or completed jobs shed their private payload.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import timedelta
from time import perf_counter
from uuid import uuid4

from amora.cognition import CognitiveRequest
from amora.events import PrivacyScope
from amora.storage.backend import execute_atomic_statement


_LOG = logging.getLogger("amora.messaging.reflection")
_REFLECTION_TIMEOUT_SECONDS = 180.0
_LEASE_SECONDS = 240
_MAX_ATTEMPTS = 5


@dataclass(frozen=True)
class ClaimedReflection:
    request: CognitiveRequest
    token: str
    attempts: int


class ReflectionQueue:
    def __init__(self, database, clock) -> None:
        self.database = database
        self.clock = clock

    def enqueue(self, request: CognitiveRequest) -> bool:
        if not request.remote_processing_allowed or request.privacy_scope in {
            PrivacyScope.AUTH, PrivacyScope.SECRET,
        } or request.metadata.get("memory_consolidation") is False:
            return False
        now = self.clock.now().isoformat()
        with self.database._lock:
            cursor = execute_atomic_statement(
                self.database.backend,
                self.database._conn,
                """INSERT INTO messaging_reflection_jobs(
                       turn_id, request_json, status, attempts, next_attempt_at,
                       claim_token, claimed_until, last_error_code, created_at, updated_at
                   ) VALUES (?, ?, 'pending', 0, ?, NULL, NULL, NULL, ?, ?)
                   ON CONFLICT(turn_id) DO NOTHING""",
                (str(request.turn_id), request.model_dump_json(), now, now, now),
            )
        return cursor.rowcount > 0

    def claim(self) -> ClaimedReflection | None:
        now_dt = self.clock.now()
        now = now_dt.isoformat()
        token = str(uuid4())
        lease = (now_dt + timedelta(seconds=_LEASE_SECONDS)).isoformat()
        # CAS in one statement: a remote read followed by UPDATE would allow
        # duplicate claims by multiple Northflank replicas.
        with self.database._lock:
            row = execute_atomic_statement(
                self.database.backend,
                self.database._conn,
                """UPDATE messaging_reflection_jobs
                   SET status='running', attempts=attempts+1, claim_token=?,
                       claimed_until=?, updated_at=?
                   WHERE turn_id=(
                       SELECT turn_id FROM messaging_reflection_jobs
                       WHERE ((status IN ('pending','retry') AND next_attempt_at<=?)
                          OR (status='running' AND claimed_until<=?))
                       ORDER BY created_at, turn_id LIMIT 1
                   )
                   AND ((status IN ('pending','retry') AND next_attempt_at<=?)
                     OR (status='running' AND claimed_until<=?))
                   RETURNING request_json, attempts""",
                (token, lease, now, now, now, now, now),
            ).fetchone()
        if row is None:
            return None
        return ClaimedReflection(
            request=CognitiveRequest.model_validate_json(row["request_json"]),
            token=token,
            attempts=int(row["attempts"]),
        )

    def settle(self, job: ClaimedReflection, *, error_code: str | None = None) -> bool:
        now_dt = self.clock.now()
        now = now_dt.isoformat()
        failed = error_code is not None
        retry = failed and job.attempts < _MAX_ATTEMPTS
        status = "retry" if retry else "failed" if failed else "completed"
        delay = min(1800, 15 * (4 ** max(0, job.attempts - 1)))
        next_at = (now_dt + timedelta(seconds=delay)).isoformat() if retry else now
        # Keep retries recoverable; erase private user text on terminal result.
        with self.database._lock:
            result = execute_atomic_statement(
                self.database.backend,
                self.database._conn,
                """UPDATE messaging_reflection_jobs
                   SET status=?, request_json=CASE WHEN ? THEN request_json ELSE NULL END,
                       next_attempt_at=?, claim_token=NULL, claimed_until=NULL,
                       last_error_code=?, updated_at=?
                   WHERE turn_id=? AND claim_token=? AND status='running'""",
                (status, int(retry), next_at, error_code, now,
                 str(job.request.turn_id), job.token),
            )
        return result.rowcount > 0

    def counts(self) -> dict[str, int]:
        with self.database._lock:
            rows = self.database._conn.execute(
                "SELECT status, COUNT(*) AS n FROM messaging_reflection_jobs GROUP BY status"
            ).fetchall()
        return {str(row["status"]): int(row["n"]) for row in rows}


class ReflectionRunner:
    def __init__(self, queue: ReflectionQueue, cognition) -> None:
        self.queue = queue
        self.cognition = cognition
        self._task: asyncio.Task | None = None
        self._wake: asyncio.Event | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._wake = asyncio.Event()
        self._task = asyncio.create_task(self._run(), name="amora-reflection-worker")
        self.notify()

    def notify(self) -> None:
        if self._wake is not None:
            self._wake.set()

    async def stop(self) -> None:
        task = self._task
        self._task = None
        if task is None:
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        self._wake = None

    async def process_one(self) -> bool:
        job = await asyncio.to_thread(self.queue.claim)
        if job is None:
            return False
        started = perf_counter()
        error_code = None
        accepted = 0
        try:
            bundle = await asyncio.wait_for(
                self.cognition.reflect(job.request),
                timeout=_REFLECTION_TIMEOUT_SECONDS,
            )
            for failure in ("REFLECTION_PROVIDER_FAILURE", "NO_STRUCTURED_REFLECTION_PROVIDER",
                            "CONTEXT_ENVELOPE_OVERFLOW"):
                if failure in bundle.reason_codes:
                    error_code = failure
                    break
            accepted = len(bundle.accepted_memory_ids)
        except asyncio.CancelledError:
            # Keep the durable lease. A restarted Core reclaims it after expiry.
            raise
        except Exception as exc:
            error_code = type(exc).__name__
        settled = await asyncio.to_thread(self.queue.settle, job, error_code=error_code)
        _LOG.info(
            "messaging_reflection_settled",
            extra={
                "turn_id": str(job.request.turn_id),
                "status": ("retry" if error_code and job.attempts < _MAX_ATTEMPTS
                           else "failed" if error_code else "completed"),
                "attempt": job.attempts,
                "error_code": error_code,
                "accepted_memory_count": accepted,
                "duration_ms": round((perf_counter() - started) * 1000),
                "lease_settled": settled,
            },
        )
        return True

    async def _run(self) -> None:
        while True:
            try:
                if await self.process_one():
                    continue
                if self._wake is not None:
                    self._wake.clear()
                    try:
                        await asyncio.wait_for(self._wake.wait(), timeout=5.0)
                    except asyncio.TimeoutError:
                        pass
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOG.warning(
                    "messaging_reflection_worker_error",
                    extra={"error_code": type(exc).__name__},
                )
                await asyncio.sleep(5.0)
