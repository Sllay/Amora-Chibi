"""Explicit SQLConnection contracts against disposable PostgreSQL 18.

CI only; tests never touch the Aiven database or private Amora memories.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from amora.storage.backend import backend_from_environment, execute_atomic_statement
from amora.storage.postgres_atomic_index_lab import put_synthetic_memory
from amora.storage.postgres_fts_bm25_lab import install_bm25_lab
from amora.storage.postgres_memory_lab import ensure_local_lab
from amora.storage.postgres_sql_connection_lab import (
    COUNT_ACTIVE, COUNT_EVENTS, FETCH_ALLOWED, FETCH_EVENT, INSERT_EVENT,
    LabCommitOutcomeUnknown, LabConnectionError, LabResult, LabRow,
    PostgresLabConnection,
)
from amora.storage.postgres_v18_lab_schema import install_pg_v18_lab

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Disposable PostgreSQL 18 loopback integration tests only",
)
TIME = "2026-11-17T00:00:00+00:00"


def connect():
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )


@pytest.fixture
def pair():
    raw = connect()
    try:
        ensure_local_lab(raw)
        raw.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        install_pg_v18_lab(raw)
        install_bm25_lab(raw)
        yield raw, PostgresLabConnection(raw)
    finally:
        try:
            if not raw.closed:
                raw.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        finally:
            raw.close()


def event_params(ident="e1", actor="BK", dedupe="dedupe-1"):
    return (ident, "synthetic-message", TIME, actor, '{"fixture":true}', dedupe)


def test_row_mapping_and_bounded_event_reads_after_commit(pair):
    _, db = pair
    assert db.in_transaction is False
    with db:
        assert db.in_transaction is True
        result = db.execute(INSERT_EVENT, event_params())
        assert result.rowcount == 1
        assert result.fetchone() is None
        assert db.execute(COUNT_EVENTS, ("BK",)).fetchone()[0] == 1
        row = db.execute(FETCH_EVENT, ("e1", "BK")).fetchone()
        assert row[0] == row["id"] == "e1"
        assert row[1] == row["event_type"] == "synthetic-message"
        assert dict(row)["actor"] == "BK"
        assert list(row) == ["id", "event_type", "timestamp", "actor"]
        assert len(row) == 4
        with pytest.raises(KeyError):
            _ = row["missing"]
    assert db.in_transaction is False
    assert row["timestamp"] == TIME
    assert db.execute(COUNT_EVENTS, ("BK",)).fetchall()[0]["total"] == 1
    assert db.execute(COUNT_EVENTS, ("OTHER",)).fetchone()[0] == 0
    result = db.execute(FETCH_EVENT, ("e1", "BK"))
    assert len(result.fetchall()) == 1
    assert result.fetchall() == []
    assert result.fetchone() is None
    assert db.execute(FETCH_EVENT, ("e1", "OTHER")).fetchone() is None
    assert db.execute(FETCH_EVENT, ("e1' OR TRUE --", "BK")).fetchone() is None


def test_execute_atomic_statement_and_idempotent_event_dedupe(pair):
    _, db = pair
    result = execute_atomic_statement(db, db, INSERT_EVENT, event_params())
    assert result.rowcount == 1
    assert db.execute(COUNT_EVENTS, ("BK",)).fetchone()[0] == 1
    assert execute_atomic_statement(
        db, db, INSERT_EVENT, event_params("different-id", "BK", "dedupe-1")
    ).rowcount == 0
    assert db.execute(COUNT_EVENTS, ("BK",)).fetchone()[0] == 1
    assert db.execute(FETCH_EVENT, ("e1", "BK")).fetchone()["id"] == "e1"


def test_multi_write_failure_rolls_back_full_transaction(pair):
    psycopg = pytest.importorskip("psycopg")
    _, db = pair
    with pytest.raises(ValueError, match="synthetic forced rollback"):
        with db:
            db.executemany(INSERT_EVENT, [
                event_params("e1", "BK", "dedupe-1"),
                event_params("e2", "BK", "dedupe-2"),
            ])
            raise ValueError("synthetic forced rollback")
    assert db.execute(COUNT_EVENTS, ("BK",)).fetchone()[0] == 0

    with db:
        db.execute(INSERT_EVENT, event_params())
        assert db.in_transaction
        with pytest.raises(psycopg.errors.UniqueViolation):
            with db:
                # Same primary key but different dedupe key: must fail,
                # rolling back the inner savepoint only.
                db.executemany(INSERT_EVENT, [
                    event_params("e2", "BK", "dedupe-2"),
                    event_params("e2", "BK", "dedupe-3"),
                ])
        assert db.execute(COUNT_EVENTS, ("BK",)).fetchone()[0] == 1
        db.execute(INSERT_EVENT, event_params("e3", "BK", "dedupe-3"))
    assert db.execute(COUNT_EVENTS, ("BK",)).fetchone()[0] == 2


def test_reject_batch_without_tx_and_raw_sqlite_dialect(pair):
    _, db = pair
    with pytest.raises(LabConnectionError, match="PG_LAB_BATCH_REQUIRES_TRANSACTION"):
        db.executemany(INSERT_EVENT, (event_params(),))
    assert db.execute(COUNT_EVENTS, ("BK",)).fetchone()[0] == 0
    for forbidden in (
        "PRAGMA foreign_keys=ON",
        "SELECT * FROM sqlite_schema",
        "CREATE TABLE amora_pg_v18_lab.attacker(id TEXT)",
        "SELECT json_extract(metadata_json,'$.turn_id') FROM memory_records",
        "SELECT id FROM memory_fts WHERE memory_fts MATCH 'rabbit'",
        "UPDATE amora_pg_v18_lab.memory_records SET content='stale index'",
        "DELETE FROM amora_pg_v18_lab.memory_search",
        "SELECT * FROM amora_pg_v18_lab.events; DROP SCHEMA amora_pg_v18_lab",
        "SELECT * FROM public.events",
        "SELECT id FROM amora_pg_v18_lab.events WHERE id=?",
        "SELECT 1",
    ):
        with pytest.raises(LabConnectionError, match="PG_LAB_SQL_NOT_REVIEWED"):
            db.execute(forbidden)
    with pytest.raises(LabConnectionError, match="PG_LAB_SQLITE_SCRIPT_UNSUPPORTED"):
        db.executescript("CREATE TABLE etc")
    with pytest.raises(LabConnectionError, match="PG_LAB_POSITIONAL_PARAMETERS_REQUIRED"):
        db.execute(FETCH_EVENT, {"id": "e1"})
    with pytest.raises(LabConnectionError, match="PG_LAB_UNSUPPORTED_PARAMETER_TYPE"):
        db.execute(FETCH_EVENT, (object(), "BK"))
    assert db.execute(COUNT_EVENTS, ("BK",)).fetchone()[0] == 0


def test_memory_reads_allowed_but_writes_require_atomic_postings_path(pair):
    raw, db = pair
    put_synthetic_memory(
        raw, memory_id="m1", owner_id="BK", content="coração da floresta",
        summary="coelho rosa", tags=("amor",), revision=TIME,
    )
    row = db.execute(FETCH_ALLOWED, ("m1", "BK")).fetchone()
    assert row["id"] == "m1"
    assert row[1] == "coração da floresta"
    assert dict(row)["owner_entity_id"] == "BK"
    assert db.execute(COUNT_ACTIVE).fetchone()[0] == 1
    assert db.execute(FETCH_ALLOWED, ("m1", "OTHER")).fetchone() is None
    assert raw.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.fts_term_postings
        WHERE memory_id='m1'
    """).fetchone()[0] >= 2
    with pytest.raises(LabConnectionError, match="PG_LAB_SQL_NOT_REVIEWED"):
        db.execute("""
            UPDATE amora_pg_v18_lab.memory_records
            SET content=%s WHERE id=%s
        """, ("unindexed", "m1"))
    assert db.execute(FETCH_ALLOWED, ("m1", "BK")).fetchone()["content"] == "coração da floresta"


def test_restart_shows_committed_event(pair):
    _, db = pair
    with db:
        db.execute(INSERT_EVENT, event_params())
    with connect() as raw2:
        observer = PostgresLabConnection(raw2)
        assert observer.execute(FETCH_EVENT, ("e1", "BK")).fetchone()[0] == "e1"
        observer.close()
        with pytest.raises(LabConnectionError, match="PG_LAB_CONNECTION_UNUSABLE"):
            observer.execute(COUNT_EVENTS, ("BK",))


def test_remote_and_prod_environment_selection_refused(monkeypatch):
    class Remote:
        info = SimpleNamespace(
            host="service.aivencloud.com", dbname="amora_pg_synthetic",
            server_version=180000,
        )
        autocommit = True
        closed = False

        def execute(self, *_args, **_kwargs):
            raise AssertionError("No SQL must be sent to Aiven")
    with pytest.raises(ValueError, match="POSTGRES_LAB_LOCAL_DB_REQUIRED"):
        PostgresLabConnection(Remote())
    monkeypatch.setenv("AMORA_STORAGE_BACKEND", "postgres")
    with pytest.raises(ValueError, match="sqlite or libsql"):
        backend_from_environment()


def test_no_implicit_transaction_or_closed_connection():
    class Invalid:
        info = SimpleNamespace(
            host="127.0.0.1", dbname="amora_pg_synthetic", server_version=180000,
        )
        closed = False
        autocommit = False
    with pytest.raises(LabConnectionError, match="PG_LAB_AUTOCOMMIT_REQUIRED"):
        PostgresLabConnection(Invalid())


def test_ambiguous_commit_poisoned_and_no_retry():
    """Failure injection: fake local transaction loses connection at COMMIT."""
    class FailedTransaction:
        def __enter__(self):
            return self

        def __exit__(self, kind, value, traceback):
            raise ConnectionError("simulated lost commit acknowledgement")

    class BrokenRaw:
        info = SimpleNamespace(
            host="127.0.0.1", dbname="amora_pg_synthetic", server_version=180001,
        )
        autocommit = True
        closed = False
        calls = 0

        def transaction(self):
            return FailedTransaction()

        def execute(self, *_args, **_kwargs):
            self.calls += 1
            raise AssertionError("no replay SQL")

        def close(self):
            self.closed = True

    raw = BrokenRaw()
    db = PostgresLabConnection(raw)
    with pytest.raises(LabCommitOutcomeUnknown, match="PG_LAB_COMMIT_OR_ROLLBACK_UNKNOWN"):
        with db:
            pass
    assert raw.closed
    assert raw.calls == 0
    with pytest.raises(LabConnectionError, match="PG_LAB_CONNECTION_UNUSABLE"):
        db.execute(COUNT_EVENTS, ("BK",))


def test_result_row_mapping_and_fetched_iterator():
    row = LabRow(("name", "value"), ("synthetic", 10))
    assert dict(row) == {"name": "synthetic", "value": 10}
    assert row[0] == "synthetic"
    assert row[-1] == 10
    data = LabResult(columns=("id",), values=[("x",), ("y",)], rowcount=2)
    assert data.fetchone()["id"] == "x"
    assert [r[0] for r in data] == ["y"]
    assert data.fetchall() == []
