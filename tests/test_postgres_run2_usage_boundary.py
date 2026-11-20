"""Best-effort cognition usage does not turn PostgreSQL failures into lost replies."""
from __future__ import annotations

import pytest

from amora.cognition.usage import UsageRecorder
from amora.storage.postgres_runtime_connection import (
    PostgresCommitUnknown,PostgresRuntimeError,
)
from amora.time import SystemClock


class FailingStore:
    def __init__(self,error):
        self.error=error

    def record_usage(self,**kwargs):
        raise self.error


def test_pg_ambiguous_commit_is_reported_uncertain_not_retried():
    recorder=UsageRecorder(
        FailingStore(PostgresCommitUnknown("PG_RUNTIME_COMMIT_OR_ROLLBACK_UNKNOWN")),
        SystemClock(),
    )
    assert recorder.record(provider="synthetic",model="invented")=="uncertain"
    state=recorder.state()
    assert state["uncertain_count"]==1
    assert state["failure_count"]==0
    assert state["status"]=="DEGRADED"


def test_pg_reviewed_sql_failure_is_definite_failed_telemetry():
    recorder=UsageRecorder(
        FailingStore(PostgresRuntimeError("PG_RUNTIME_SQL_NOT_REVIEWED")),
        SystemClock(),
    )
    assert recorder.record(provider="synthetic",model="invented")=="failed"
    assert recorder.state()["failure_count"]==1


def test_unrelated_application_bugs_are_not_swallowed():
    recorder=UsageRecorder(FailingStore(ValueError("program bug")),SystemClock())
    with pytest.raises(ValueError,match="program bug"):
        recorder.record(provider="synthetic",model="invented")
    assert recorder.state()["status"]=="ON"


def test_native_psycopg_constraint_is_known_failed_telemetry():
    psycopg=pytest.importorskip("psycopg")
    recorder=UsageRecorder(
        FailingStore(psycopg.errors.UniqueViolation("synthetic duplicate")),
        SystemClock(),
    )
    assert recorder.record(provider="synthetic",model="invented")=="failed"
    assert recorder.state()["failure_count"]==1
