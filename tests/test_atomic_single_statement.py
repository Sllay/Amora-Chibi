from __future__ import annotations

from pathlib import Path

import httpx

from amora.storage.backend import SQLiteBackend, execute_atomic_statement
from amora.storage.remote import LibSQLBackend
from test_remote_storage import HranaSQLite


class CountingHrana(HranaSQLite):
    def __init__(self, path: Path):
        super().__init__(path)
        self.pipeline_calls = 0

    def __call__(self, request):
        self.pipeline_calls += 1
        return super().__call__(request)


def test_libsql_atomic_single_statement_uses_one_pipeline(tmp_path: Path) -> None:
    server = CountingHrana(tmp_path / "atomic-remote.db")
    backend = LibSQLBackend(
        "https://database.example",
        "test-only",
        transport=httpx.MockTransport(server),
    )
    connection = backend.connect(tmp_path / "unused.db")
    try:
        connection.execute("CREATE TABLE probe(value INTEGER)")
        server.pipeline_calls = 0

        result = execute_atomic_statement(
            backend,
            connection,
            "INSERT INTO probe(value) VALUES (?)",
            (7,),
        )

        assert result.rowcount == 1
        assert server.pipeline_calls == 1
        assert connection.execute("SELECT value FROM probe").fetchone()[0] == 7
    finally:
        connection.close()


def test_sqlite_atomic_single_statement_commits_before_reopen(tmp_path: Path) -> None:
    path = tmp_path / "atomic-local.db"
    backend = SQLiteBackend()
    connection = backend.connect(path)
    try:
        with connection:
            connection.execute("CREATE TABLE probe(value INTEGER)")

        result = execute_atomic_statement(
            backend,
            connection,
            "INSERT INTO probe(value) VALUES (?)",
            (11,),
        )
        assert result.rowcount == 1
    finally:
        connection.close()

    reopened = backend.connect(path)
    try:
        assert reopened.execute("SELECT value FROM probe").fetchone()[0] == 11
    finally:
        reopened.close()


def test_atomic_helper_respects_existing_local_transaction(tmp_path: Path) -> None:
    path = tmp_path / "atomic-nested.db"
    backend = SQLiteBackend()
    connection = backend.connect(path)
    try:
        with connection:
            connection.execute("CREATE TABLE probe(value INTEGER)")
        try:
            with connection:
                execute_atomic_statement(
                    backend,
                    connection,
                    "INSERT INTO probe(value) VALUES (?)",
                    (13,),
                )
                raise RuntimeError("rollback outer unit")
        except RuntimeError:
            pass

        assert connection.execute("SELECT COUNT(*) FROM probe").fetchone()[0] == 0
    finally:
        connection.close()
