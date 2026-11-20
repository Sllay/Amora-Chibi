"""Run 1 PostgreSQL persistence infrastructure contract (no Aiven credentials).

These tests use a fake psycopg connection. They are deliberately NOT evidence
that the Core domains or real PostgreSQL storage are certified.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from amora.storage.backend import (
    StorageCapabilities, backend_from_environment, execute_atomic_statement,
)
from amora.storage.database import AmoraDatabase
from amora.storage.postgres_runtime_connection import (
    PostgresBackendCandidate, PostgresCommitUnknown, PostgresResult,
    PostgresRow, PostgresRuntimeConnection, PostgresRuntimeError,
    PostgresTargetPolicy, ReviewedStatement,
)


SQL = "SELECT value FROM inventory WHERE id=?"
REGISTRY = {SQL: ReviewedStatement(
    "SELECT value FROM amora_pg_v18_lab.inventory WHERE id=%s", 1,
)}


class FakeCursor:
    def __init__(self, owner):
        self.owner = owner
        self.description = None
        self.rowcount = 0
        self.rows = []

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def execute(self, sql, params=()):
        self.owner.calls.append((sql, tuple(params)))
        if "pg_catalog.pg_tables" in sql:
            self.rows = [(name,) for name in sorted(self.owner.tables)]
        elif "information_schema.columns" in sql:
            self.rows = [
                (table, column)
                for table, columns in sorted(self.owner.columns.items())
                for column in columns
            ]
        elif "schema_migrations" in sql and "ORDER BY version" in sql:
            self.rows = [(value,) for value in self.owner.versions]
        elif sql.startswith("SELECT"):
            self.rows = [("safe synthetic value",)]
        else:
            self.rows = []
        self.rowcount = len(self.rows)
        self.description = (
            [SimpleNamespace(name="value")] if sql.startswith("SELECT") else None
        )
        return self

    def executemany(self, sql, params):
        self.owner.calls.append((sql, tuple(params)))
        self.rowcount = len(params)

    def fetchmany(self, size):
        result, self.rows = self.rows[:size], self.rows[size:]
        return result

    def fetchall(self):
        result, self.rows = self.rows, []
        return result


class FakeTransaction:
    def __init__(self, owner):
        self.owner = owner

    def __enter__(self):
        self.owner.tx_depth += 1
        return self

    def __exit__(self, *_args):
        self.owner.tx_depth -= 1
        if self.owner.fail_commit:
            raise ConnectionError("invented ambiguous commit")
        return False


class FakeRaw:
    def __init__(self, *, host="127.0.0.1", sslmode=None, ssl=False):
        self.info = SimpleNamespace(
            host=host, port=5432, dbname="amora_pg_synthetic",
            server_version=180000,
            get_parameters=lambda: {"sslmode": sslmode},
        )
        self.pgconn = SimpleNamespace(ssl_in_use=ssl)
        self.autocommit = True
        self.closed = False
        self.calls = []
        self.tx_depth = 0
        self.fail_commit = False
        self.tables = set()
        self.columns = {}
        self.versions = tuple(range(1, 19))

    def cursor(self):
        return FakeCursor(self)

    def transaction(self):
        return FakeTransaction(self)

    def close(self):
        self.closed = True


def test_exact_query_binding_and_sqlite_row_compatibility():
    raw = FakeRaw()
    db = PostgresRuntimeConnection(raw, REGISTRY)
    row = db.execute(SQL, ("m1",)).fetchone()
    assert row[0] == row["value"] == "safe synthetic value"
    assert dict(row) == {"value": "safe synthetic value"}
    assert raw.calls[-1] == (REGISTRY[SQL].postgres_sql, ("m1",))
    with pytest.raises(PostgresRuntimeError, match="PG_RUNTIME_SQL_NOT_REVIEWED"):
        db.execute("PRAGMA foreign_keys=OFF")
    with pytest.raises(PostgresRuntimeError, match="PG_RUNTIME_PARAMETER_COUNT"):
        db.execute(SQL, ())
    assert len(raw.calls) == 1
    db.close()


def test_result_iterator_consumption():
    row = PostgresRow(("id", "count"), ("a", 2))
    assert tuple(row) == ("id", "count")
    assert row[-1] == 2
    with pytest.raises(KeyError):
        _ = row["missing"]
    result = PostgresResult(("id",), (("a",), ("b",)), 2)
    assert result.fetchone()["id"] == "a"
    assert [item["id"] for item in result] == ["b"]
    assert result.fetchall() == []


def test_transactions_nested_and_atomic_write_contract():
    raw = FakeRaw()
    backend = PostgresBackendCandidate(
        lambda: raw,
        {"UPDATE balance SET value=?": ReviewedStatement(
            "UPDATE amora_pg_v18_lab.balance SET value=%s", 1,
        )},
    )
    db = backend.connect(Path("ignored"))
    assert db.in_transaction is False
    with db:
        assert db.in_transaction is True
        with db:
            assert raw.tx_depth == 2
        execute_atomic_statement(
            backend, db, "UPDATE balance SET value=?", (7,),
        )
    assert db.in_transaction is False
    assert raw.tx_depth == 0
    assert raw.calls[-1][-1] == (7,)
    db.close()
    assert backend.opened_connections == 0


def test_budget_and_failed_connection_release():
    backend = PostgresBackendCandidate(
        FakeRaw, REGISTRY, max_connections=1,
    )
    first = backend.connect(Path("ignored"))
    assert backend.opened_connections == 1
    with pytest.raises(PostgresRuntimeError, match="PG_RUNTIME_CONNECTION_BUDGET_EXCEEDED"):
        backend.connect(Path("ignored"))
    first.close()
    first.close()
    assert backend.opened_connections == 0
    second = backend.connect(Path("ignored"))
    second.close()

    rejected = PostgresBackendCandidate(
        lambda: FakeRaw(host="unapproved.example"), REGISTRY,
    )
    with pytest.raises(PostgresRuntimeError, match="PG_RUNTIME_DESTINATION_MISMATCH"):
        rejected.connect(Path("ignored"))
    assert rejected.opened_connections == 0


def test_remote_target_requires_verified_tls_and_exact_identity():
    with pytest.raises(ValueError, match="PG_RUNTIME_REMOTE_TLS_REQUIRED"):
        PostgresTargetPolicy(host="service.example", database="amora_pg_synthetic")
    remote = PostgresTargetPolicy(
        host="service.example", database="amora_pg_synthetic",
        require_tls=True,
    )
    with pytest.raises(PostgresRuntimeError, match="PG_RUNTIME_TLS_NOT_VERIFIED"):
        remote.verify(FakeRaw(host="service.example"))
    remote.verify(FakeRaw(
        host="service.example", sslmode="verify-full", ssl=True,
    ))


def test_ambiguous_commit_closes_and_releases_lease():
    raw = FakeRaw()
    raw.fail_commit = True
    backend = PostgresBackendCandidate(lambda: raw, REGISTRY)
    db = backend.connect(Path("ignored"))
    with pytest.raises(PostgresCommitUnknown, match="PG_RUNTIME_COMMIT_OR_ROLLBACK_UNKNOWN"):
        with db:
            pass
    assert raw.closed
    assert backend.opened_connections == 0
    with pytest.raises(PostgresRuntimeError, match="PG_RUNTIME_CONNECTION_UNUSABLE"):
        db.execute(SQL, ("x",))


def test_prepared_schema_is_strictly_read_only():
    from amora.storage.postgres_v18_manifest import build_manifest

    raw = FakeRaw()
    manifest = build_manifest()
    raw.columns = {
        table["name"]: tuple(c["name"] for c in table["columns"])
        for table in manifest["tables"] if table["kind"] == "ordinary"
    }
    raw.columns["memory_search"] = ("memory_id", "content", "tags")
    raw.tables = set(raw.columns)
    db = PostgresRuntimeConnection(raw, REGISTRY)
    db.verify_prepared_schema(18)
    assert all(sql.startswith("SELECT") for sql, _ in raw.calls)
    with pytest.raises(PostgresRuntimeError, match="PG_RUNTIME_SCHEMA_TARGET_UNSUPPORTED"):
        db.verify_prepared_schema(19)
    raw.columns["memory_search"] = ("memory_id", "content")
    with pytest.raises(PostgresRuntimeError, match="PG_RUNTIME_SCHEMA_COLUMNS_MISMATCH"):
        db.verify_prepared_schema(18)
    raw.columns["memory_search"] = ("memory_id", "content", "tags")
    raw.versions = (1, 2)
    with pytest.raises(PostgresRuntimeError, match="PG_RUNTIME_SCHEMA_VERSION_MISMATCH"):
        db.verify_prepared_schema(18)
    db.close()


def test_database_migration_uses_pg_readiness_guard_not_sqlite_ddl(tmp_path):
    class FakeConnection:
        def verify_prepared_schema(self, expected):
            assert expected == 18

        def execute(self, *_args):
            raise AssertionError("SQLite DDL must never run")

        def close(self):
            pass

    class FakeBackend:
        capabilities = StorageCapabilities("postgres-candidate", False, False, True)

        def connect(self, _path):
            return FakeConnection()

    database = AmoraDatabase(tmp_path / "synthetic.db", backend=FakeBackend())
    assert database.migrate() == 0
    database.close()


def test_production_selector_fails_closed_without_independent_approval(monkeypatch):
    monkeypatch.setenv("AMORA_STORAGE_BACKEND", "postgres")
    monkeypatch.delenv("AMORA_PG_PRODUCTION_APPROVAL", raising=False)
    with pytest.raises(PostgresRuntimeError, match="PG_PRODUCTION_CUTOVER_APPROVAL_REQUIRED"):
        backend_from_environment()


def test_batch_requires_transaction_and_caps_size():
    write = "INSERT INTO synthetic_events(id) VALUES (?)"
    backend = PostgresBackendCandidate(FakeRaw, {
        write: ReviewedStatement(
            "INSERT INTO amora_pg_v18_lab.synthetic_events(id) VALUES (%s)",
            1, batch_allowed=True,
        ),
    })
    db = backend.connect(Path("unused"))
    with pytest.raises(PostgresRuntimeError, match="PG_RUNTIME_BATCH_REQUIRES_TRANSACTION"):
        db.executemany(write, (("a",),))
    with db:
        assert db.executemany(write, (("a",), ("b",))) == 2
        with pytest.raises(PostgresRuntimeError, match="PG_RUNTIME_BATCH_LIMIT_EXCEEDED"):
            db.executemany(write, (("x",) for _ in range(257)))
    db.close()


def test_concurrent_thread_does_not_interleave_into_transaction():
    from threading import Event, Thread

    raw = FakeRaw()
    db = PostgresRuntimeConnection(raw, REGISTRY)
    started, completed = Event(), Event()

    def worker():
        started.set()
        db.execute(SQL, ("worker",))
        completed.set()

    with db:
        thread = Thread(target=worker, daemon=True)
        thread.start()
        assert started.wait(2)
        # A concurrent statement cannot execute before the owner commits.
        assert not completed.wait(0.05)
        assert raw.calls == []
    thread.join(2)
    assert completed.is_set()
    assert raw.calls == [(REGISTRY[SQL].postgres_sql, ("worker",))]
    db.close()


def test_target_rejects_wrong_schema_and_pg_version():
    with pytest.raises(ValueError, match="PG_RUNTIME_INVALID_TARGET"):
        PostgresTargetPolicy(schema="other-schema")
    with pytest.raises(ValueError, match="PG_RUNTIME_INVALID_TARGET"):
        PostgresTargetPolicy(server_major=17)


def test_run1_real_pg_adapter_bootstrap_with_disposable_v18_fixture(tmp_path):
    """End-to-end backend adapter preflight against real local PG18 only."""
    import os
    from contextlib import closing

    if os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1":
        pytest.skip("Only a disposable local PG18 CI service is permitted")
    psycopg = pytest.importorskip("psycopg")
    from amora.storage.postgres_runtime_synthetic_setup import (
        prepare_disposable_core_v18,
    )
    from amora.storage.postgres_runtime_registry import synthetic_core_backend
    from amora.storage.postgres_runtime_memory import READY

    def raw_connection():
        return psycopg.connect(
            host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
            user="amora_ci",password="synthetic_only",
            connect_timeout=5,autocommit=True,
        )

    with closing(raw_connection()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            preparation = prepare_disposable_core_v18(manager)
            assert preparation["production_backend_switched"] is False
            backend = synthetic_core_backend(raw_connection,max_connections=2)
            with closing(AmoraDatabase(
                tmp_path / "no-sqlite-backup.db",backend=backend,
            )) as database:
                assert database.migrate() == 0
                assert database.schema_version() == 18
                assert database._conn.execute(READY).fetchone()[0] is True
                assert database.event_count() >= 0
            assert backend.opened_connections == 0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
