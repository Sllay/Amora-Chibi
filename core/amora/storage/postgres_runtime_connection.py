"""PostgreSQL Core SQLConnection candidate (not wired to production).

PostgreSQL statements are explicitly reviewed at every call site. This module
does not rewrite SQLite SQL or change AMORA_STORAGE_BACKEND. The caller owns
the psycopg connection and must supply a reviewed statement registry; a
missing mapping fails closed. The implementation provides DB-API-like rows,
nested transaction/savepoint semantics and ambiguous-commit protection.

Release gates remain outside this module: all Core query families, memory
ranking, authorization, schema migration, rollback and integration testing.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any


class PostgresRuntimeError(RuntimeError):
    """Safe stable error codes: never include SQL, parameters or credentials."""


class PostgresCommitUnknown(PostgresRuntimeError):
    """Commit outcome may be ambiguous; a failed write must not be replayed."""


@dataclass(frozen=True)
class ReviewedStatement:
    """One explicitly translated and audited Core SQL statement."""

    postgres_sql: str
    parameter_count: int
    max_result_rows: int = 1000
    batch_allowed: bool = False

    def __post_init__(self) -> None:
        if not self.postgres_sql or not self.postgres_sql.strip():
            raise ValueError("PG_RUNTIME_EMPTY_STATEMENT")
        if self.parameter_count < 0 or self.max_result_rows < 1:
            raise ValueError("PG_RUNTIME_INVALID_STATEMENT_LIMIT")


@dataclass(frozen=True)
class PostgresRow(Mapping[str, Any]):
    """Supports SQLite-style row[0], row['name'] and dict(row)."""

    columns: tuple[str, ...]
    values: tuple[Any, ...]

    def __getitem__(self, key: str | int) -> Any:
        if isinstance(key, int):
            return self.values[key]
        try:
            return self.values[self.columns.index(key)]
        except ValueError:
            raise KeyError(key) from None

    def __iter__(self) -> Iterator[str]:
        return iter(self.columns)

    def __len__(self) -> int:
        return len(self.columns)


class PostgresResult:
    """Materialized cursor result; remains readable after transaction exit."""

    def __init__(
        self, columns: tuple[str, ...], values: Iterable[tuple[Any, ...]],
        rowcount: int,
    ) -> None:
        self._rows = [PostgresRow(columns, tuple(row)) for row in values]
        self._position = 0
        self.rowcount = rowcount

    def fetchone(self) -> PostgresRow | None:
        if self._position >= len(self._rows):
            return None
        result = self._rows[self._position]
        self._position += 1
        return result

    def fetchall(self) -> list[PostgresRow]:
        result = self._rows[self._position:]
        self._position = len(self._rows)
        return result

    def __iter__(self) -> Iterator[PostgresRow]:
        while (row := self.fetchone()) is not None:
            yield row


class PostgresRuntimeConnection:
    """Synchronous psycopg3 adapter for *reviewed* Core statements only.

    It is intentionally not a StorageBackend: no production connection factory,
    secrets, migrations, DSN selector or automatic PostgreSQL activation.
    Autocommit is required so explicit with-blocks determine transaction
    boundaries. psycopg nested transaction blocks provide savepoints.
    """

    def __init__(
        self, raw: Any, statements: Mapping[str, ReviewedStatement],
    ) -> None:
        if getattr(raw, "closed", True) or getattr(raw, "autocommit", None) is not True:
            raise PostgresRuntimeError("PG_RUNTIME_AUTOCOMMIT_REQUIRED")
        if not statements:
            raise PostgresRuntimeError("PG_RUNTIME_NO_REVIEWED_SQL")
        if any(not isinstance(key, str) or not key for key in statements):
            raise PostgresRuntimeError("PG_RUNTIME_INVALID_SQL_REGISTRY")
        if any(not isinstance(value, ReviewedStatement) for value in statements.values()):
            raise PostgresRuntimeError("PG_RUNTIME_INVALID_SQL_REGISTRY")
        self._raw = raw
        self._statements = dict(statements)
        self._transactions: list[Any] = []
        self._closed = False
        self._broken = False

    @property
    def in_transaction(self) -> bool:
        return bool(self._transactions)

    def _ensure_ready(self) -> None:
        if self._closed or self._broken or getattr(self._raw, "closed", True):
            raise PostgresRuntimeError("PG_RUNTIME_CONNECTION_UNUSABLE")

    def _review(self, sql: str) -> ReviewedStatement:
        if not isinstance(sql, str):
            raise PostgresRuntimeError("PG_RUNTIME_SQL_NOT_REVIEWED")
        statement = self._statements.get(sql)
        if statement is None:
            raise PostgresRuntimeError("PG_RUNTIME_SQL_NOT_REVIEWED")
        return statement

    @staticmethod
    def _parameters(values: Iterable[Any], count: int) -> tuple[Any, ...]:
        if isinstance(values, (str, bytes, bytearray, dict)):
            raise PostgresRuntimeError("PG_RUNTIME_POSITIONAL_PARAMS_REQUIRED")
        try:
            parameters = tuple(values)
        except TypeError as exc:
            raise PostgresRuntimeError("PG_RUNTIME_POSITIONAL_PARAMS_REQUIRED") from exc
        if len(parameters) != count:
            raise PostgresRuntimeError("PG_RUNTIME_PARAMETER_COUNT")
        # Preserve the integer 0/1 representation used by the SQLite Core.
        return tuple(int(value) if type(value) is bool else value for value in parameters)

    @staticmethod
    def _materialize(cursor: Any, max_rows: int) -> PostgresResult:
        count = cursor.rowcount
        if cursor.description is None:
            return PostgresResult((), (), count)
        columns = tuple(column.name for column in cursor.description)
        if len(set(columns)) != len(columns):
            raise PostgresRuntimeError("PG_RUNTIME_DUPLICATE_RESULT_COLUMNS")
        rows = cursor.fetchmany(max_rows + 1)
        if len(rows) > max_rows:
            raise PostgresRuntimeError("PG_RUNTIME_RESULT_LIMIT_EXCEEDED")
        return PostgresResult(columns, rows, count)

    def execute(self, sql: str, parameters: Iterable[Any] = ()) -> PostgresResult:
        self._ensure_ready()
        statement = self._review(sql)
        params = self._parameters(parameters, statement.parameter_count)
        with self._raw.cursor() as cursor:
            cursor.execute(statement.postgres_sql, params)
            return self._materialize(cursor, statement.max_result_rows)

    def executemany(
        self, sql: str, values: Iterable[Iterable[Any]],
    ) -> int:
        self._ensure_ready()
        statement = self._review(sql)
        if not statement.batch_allowed:
            raise PostgresRuntimeError("PG_RUNTIME_BATCH_SQL_NOT_REVIEWED")
        if not self.in_transaction:
            raise PostgresRuntimeError("PG_RUNTIME_BATCH_REQUIRES_TRANSACTION")
        if isinstance(values, (str, bytes, bytearray, dict)):
            raise PostgresRuntimeError("PG_RUNTIME_BATCH_PARAMS_REQUIRED")
        rows = []
        for values_row in values:
            if len(rows) >= 256:
                raise PostgresRuntimeError("PG_RUNTIME_BATCH_LIMIT_EXCEEDED")
            rows.append(self._parameters(values_row, statement.parameter_count))
        with self._raw.cursor() as cursor:
            cursor.executemany(statement.postgres_sql, rows)
            return cursor.rowcount

    def executescript(self, _script: str) -> None:
        raise PostgresRuntimeError("PG_RUNTIME_SQLITE_SCRIPT_UNSUPPORTED")

    def __enter__(self):
        self._ensure_ready()
        transaction = self._raw.transaction()
        try:
            transaction.__enter__()
        except BaseException:
            self._broken = True
            self._raw.close()
            raise
        self._transactions.append(transaction)
        return self

    def __exit__(self, kind, value, traceback):
        if not self._transactions:
            raise PostgresRuntimeError("PG_RUNTIME_TRANSACTION_NOT_OPEN")
        transaction = self._transactions.pop()
        try:
            return transaction.__exit__(kind, value, traceback)
        except BaseException as exc:
            self._broken = True
            try:
                self._raw.close()
            finally:
                raise PostgresCommitUnknown("PG_RUNTIME_COMMIT_OR_ROLLBACK_UNKNOWN") from exc

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._raw.close()
