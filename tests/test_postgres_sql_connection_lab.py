"""DB-API/transaction semantics versus disposable PostgreSQL 18.

Opt-in lab: no real Amora memory, Aiven DSN, production Core or Turso.
"""
from __future__ import annotations

from types import SimpleNamespace

import os
import pytest

from amora.storage.backend import backend_from_environment, execute_atomic_statement
from amora.storage.postgres_sql_connection_lab import (
    COUNT_ACTIVE, FETCH_ALLOWED, INSERT_MEMORY, UPDATE_CAS,
    INSERT_TAG, SELECT_TAGS, DELETE_TAGS,
    LabConnectionError, LabResult, LabRow, PostgresLabConnection,
)
from amora.storage.postgres_v18_lab_schema import install_pg_v18_lab
from amora.storage.postgres_memory_lab import ensure_local_lab

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Disposable PostgreSQL 18 loopback integration tests only",
)
TIME = "2026-11-17T00:00:00+00:00"
TIME2 = "2026-11-17T00:01:00+00:00"


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
        yield raw, PostgresLabConnection(raw)
    finally:
        try:
            raw.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        finally:
            raw.close()


def insert(adapter, ident="m1", owner="BK", text="coelho", t=TIME):
    return adapter.execute(INSERT_MEMORY, (ident, text, owner, owner, t, t))


def test_rows_access_by_index_column_and_dict_after_commit(pair):
    _, db = pair
    assert db.in_transaction is False
    with db:
        assert db.in_transaction is True
        result = insert(db)
        assert result.rowcount == 1
        assert result.fetchone() is None
        assert db.execute(COUNT_ACTIVE).fetchone()[0] == 1
        row = db.execute(FETCH_ALLOWED, ("m1", "BK")).fetchone()
        assert row[0] == row["id"] == "m1"
        assert row["content"] == row[1] == "coelho"
        assert dict(row)["owner_entity_id"] == "BK"
        assert list(row) == ["id", "content", "updated_at", "owner_entity_id"]
        assert len(row) == 4
    assert db.in_transaction is False
    assert row["updated_at"] == TIME
    assert db.execute(COUNT_ACTIVE).fetchall()[0]["total"] == 1
    result = db.execute(FETCH_ALLOWED, ("m1", "BK"))
    assert len(result.fetchall()) == 1
    assert result.fetchall() == []
    assert result.fetchone() is None
    assert db.execute(FETCH_ALLOWED, ("m1", "OTHER")).fetchone() is None
    assert db.execute(FETCH_ALLOWED, ("m1' OR TRUE --", "BK")).fetchone() is None


def test_execute_atomic_statement_context_and_cas(pair):
    _, db = pair
    result = execute_atomic_statement(db, db, INSERT_MEMORY,
                                      ("m1", "initial", "BK", "BK", TIME, TIME))
    assert result.rowcount == 1
    assert db.execute(COUNT_ACTIVE).fetchone()[0] == 1
    updated = execute_atomic_statement(
        db, db, UPDATE_CAS, ("new", TIME2, "m1", "BK", TIME)
    )
    assert updated.rowcount == 1
    assert updated.fetchone()[0] == "m1"
    assert execute_atomic_statement(
        db, db, UPDATE_CAS, ("lost update", TIME2, "m1", "BK", TIME)
    ).rowcount == 0
    assert db.execute(FETCH_ALLOWED, ("m1", "BK")).fetchone()["content"] == "new"


def test_rollback_whole_transaction_and_nested_savepoint(pair):
    psycopg = pytest.importorskip("psycopg")
    _, db = pair
    with pytest.raises(ValueError, match="synthetic forced rollback"):
        with db:
            insert(db)
            db.executemany(INSERT_TAG, (("m1", "forest"), ("m1", "rabbit")))
            raise ValueError("synthetic forced rollback")
    assert db.execute(COUNT_ACTIVE).fetchone()[0] == 0
    with db:
        insert(db)
        assert db.in_transaction is True
        with pytest.raises(psycopg.errors.UniqueViolation):
            with db:
                db.executemany(INSERT_TAG, (("m1", "repeated"), ("m1", "repeated")))
        # Inner savepoint rolls back, but outer transaction can commit.
        assert db.execute(SELECT_TAGS, ("m1",)).fetchall() == []
        db.executemany(INSERT_TAG, (("m1", "final"),))
    assert db.in_transaction is False
    assert [row["tag"] for row in db.execute(SELECT_TAGS, ("m1",))] == ["final"]


def test_single_statement_autocommit_and_failed_batch_rejection(pair):
    _, db = pair
    insert(db)
    with pytest.raises(LabConnectionError, match="PG_LAB_BATCH_REQUIRES_TRANSACTION"):
        db.executemany(INSERT_TAG, (("m1", "should-not-write"),))
    assert db.execute(SELECT_TAGS, ("m1",)).fetchall() == []
    with db:
        assert db.execute(DELETE_TAGS, ("m1",)).rowcount == 0
        db.executemany(INSERT_TAG, (("m1", "forest"), ("m1", "rabbit")))
    assert db.execute(SELECT_TAGS, ("m1",)).fetchall()[0][0] == "forest"


def test_raw_sqlite_queries_must_be_individually_migrated(pair):
    _, db = pair
    for forbidden in (
        "PRAGMA foreign_keys=ON",
        "SELECT * FROM sqlite_schema",
        "CREATE TABLE amora_pg_v18_lab.attacker(id TEXT)",
        "SELECT json_extract(metadata_json,'$.turn_id') FROM memory_records",
        "SELECT id FROM memory_fts WHERE memory_fts MATCH 'rabbit'",
        "SELECT * FROM amora_pg_v18_lab.memory_records; DROP SCHEMA amora_pg_v18_lab",
        "SELECT * FROM public.some_table",
        "SELECT id FROM amora_pg_v18_lab.memory_records WHERE id=?",
        "SELECT 1",
    ):
        with pytest.raises(LabConnectionError, match="PG_LAB_SQL_NOT_REVIEWED"):
            db.execute(forbidden)
    with pytest.raises(LabConnectionError, match="PG_LAB_SQLITE_SCRIPT_UNSUPPORTED"):
        db.executescript("CREATE TABLE etc")
    with pytest.raises(LabConnectionError, match="PG_LAB_POSITIONAL_PARAMETERS_REQUIRED"):
        db.execute(FETCH_ALLOWED, {"id": "m1"})
    with pytest.raises(LabConnectionError, match="PG_LAB_UNSUPPORTED_PARAMETER_TYPE"):
        db.execute(FETCH_ALLOWED, (object(), "BK"))
    assert db.execute(COUNT_ACTIVE).fetchone()[0] == 0


def test_connection_reopened_after_commit_and_close(pair):
    _, db = pair
    with db:
        insert(db, text="persisted")
    with connect() as secondary:
        observer = PostgresLabConnection(secondary)
        assert observer.execute(FETCH_ALLOWED, ("m1", "BK")).fetchone()[1] == "persisted"
        observer.close()
        with pytest.raises(LabConnectionError, match="PG_LAB_CONNECTION_UNUSABLE"):
            observer.execute(COUNT_ACTIVE)


def test_remote_and_implicit_pg_selector_are_rejected(monkeypatch):
    class Remote:
        info = SimpleNamespace(
            host="aiven.example.com", dbname="amora_pg_synthetic",
            server_version=180001,
        )
        autocommit = True
        closed = False

        def execute(self, *_args):
            raise AssertionError("PG adapter must reject remote before SQL")
    with pytest.raises(ValueError, match="POSTGRES_LAB_LOCAL_DB_REQUIRED"):
        PostgresLabConnection(Remote())

    monkeypatch.setenv("AMORA_STORAGE_BACKEND", "postgres")
    with pytest.raises(ValueError, match="sqlite or libsql"):
        backend_from_environment()


def test_fail_closed_inactive_pg_connection():
    class Invalid:
        info = SimpleNamespace(host="127.0.0.1", dbname="amora_pg_synthetic",
                               server_version=180000)
        closed = False
        autocommit = False
    with pytest.raises(LabConnectionError, match="PG_LAB_AUTOCOMMIT_REQUIRED"):
        PostgresLabConnection(Invalid())


def test_result_and_row_shape_has_no_undocumented_column_fallback():
    row = LabRow(("name", "value"), ("synthetic", 10))
    assert dict(row) == {"name": "synthetic", "value": 10}
    assert row[0] == "synthetic"
    assert row[-1] == 10
    data = LabResult(columns=("id",), values=[("x",), ("y",)], rowcount=2)
    assert data.fetchone()["id"] == "x"
    assert [r[0] for r in data] == ["y"]
    assert data.fetchall() == []
