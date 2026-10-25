"""Best-effort telemetry boundary; conversation/journal writes stay fail-closed."""

from __future__ import annotations

import logging
import sqlite3
from threading import RLock
from typing import TYPE_CHECKING, Any, Literal

from amora.observability import request_failure_fields
from amora.operations import OperationMonitor, identifier
from amora.time import Clock

if TYPE_CHECKING:
    from .store import CognitiveStore

UsagePersistence = Literal["persisted", "uncertain", "failed"]

logger = logging.getLogger(__name__)


class UsageRecorder:
    def __init__(
        self,
        store: CognitiveStore,
        clock: Clock,
        *,
        operations: OperationMonitor | None = None,
    ) -> None:
        self.store = store
        self.clock = clock
        self.operations = operations
        self._lock = RLock()
        self._state = {
            "status": "ON",
            "uncertain_count": 0,
            "failure_count": 0,
            "last_error_code": None,
            "last_failure_at": None,
            "last_success_at": None,
            "last_turn_id": None,
            "retention": "bounded-process-local",
        }

    def state(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._state)

    def record(self, **usage: Any) -> UsagePersistence:
        try:
            self.store.record_usage(**usage)
        except sqlite3.Error as exc:
            fields = request_failure_fields(exc)
            storage_code = fields.get("storage_error_code", "SQL_ERROR")
            known_failure = (
                isinstance(exc, sqlite3.IntegrityError)
                or storage_code.startswith("SQLITE_CONSTRAINT")
                or storage_code
                in {"SQL_CONNECTION_CLOSED", "SQL_CONNECTION_RECOVERY_FAILED"}
            )
            # Treat all other DB-API failures conservatively. A local I/O error
            # at COMMIT may also be ambiguous; this boundary is backend-neutral.
            uncertain = not known_failure
            status = "uncertain" if uncertain else "failed"
            code = (
                "USAGE_LEDGER_PERSISTENCE_UNCERTAIN"
                if uncertain
                else "USAGE_LEDGER_PERSISTENCE_FAILED"
            )
            turn_id = identifier(usage.get("turn_id"))
            with self._lock:
                self._state["status"] = "DEGRADED"
                self._state["uncertain_count" if uncertain else "failure_count"] += 1
                self._state.update(
                    {
                        "last_error_code": code,
                        "last_failure_at": self.clock.now().isoformat(),
                        "last_turn_id": turn_id,
                    }
                )
            # No exception text/traceback, kwargs, SQL, tokens or provider output.
            logger.warning(
                code.lower(),
                extra={
                    "error_code": code,
                    "exception_type": type(exc).__name__,
                    "turn_id": turn_id,
                    "outcome": "unknown" if uncertain else "not_recorded",
                    **fields,
                },
            )
            if self.operations is not None:
                self.operations.error("cognition.usage", code, correlation_id=turn_id)
            return status
        with self._lock:
            # A later confirmed write does not reconcile an earlier uncertain one.
            self._state["last_success_at"] = self.clock.now().isoformat()
        return "persisted"
