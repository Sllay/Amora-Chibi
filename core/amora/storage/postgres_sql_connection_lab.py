"""Phase 2E.1: explicit PostgreSQL SQLConnection *contract lab*.

An isolated, opt-in wrapper around a caller-provided psycopg connection.
NO DSN, connection factory, environment selector, production backend,
SQLite-to-PostgreSQL SQL rewriting or PCS4C backup input.

Only a small, fixed set of PostgreSQL-v18 statements is accepted. This
demonstrates the Core DB-API row and transaction contract; it deliberately
DOES NOT imply that all Amora stores can run on PostgreSQL.

The wrapped connection must already be connected to the disposable
loopback PostgreSQL 18 database named 'amora_pg_synthetic', with autocommit
enabled. Every operation re-checks that destination before any SQL.
"""
from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from typing import Any

from .backend import StorageCapabilities
from .postgres_memory_lab import ensure_local_lab

# EXACT REVIEWED SQL ONLY. No sqlite_schema, FTS5/MATCH, JSON1, PRAGMA,
# positional '?' rewriting, dynamic SQL, DDL or search_path changes.
# This is a tiny contract slice, not a general Core dialect.
COUNT_ACTIVE = """
SELECT COUNT(*) AS total FROM amora_pg_v18_lab.memory_records
WHERE archived_at IS NULL
""".strip()
FETCH_ALLOWED = """
SELECT id,content,updated_at,owner_entity_id
FROM amora_pg_v18_lab.memory_records
WHERE id=%s AND owner_entity_id=%s
  AND privacy_scope NOT IN ('AUTH','SECRET') AND archived_at IS NULL
""".strip()
# Event writes do not have dependent BM25/index documents. Deliberately
# omit any MemoryStore text/tag UPDATE from this low-level lab adapter:
# those writes MUST go through the atomic posting maintenance path.
INSERT_EVENT = """
INSERT INTO amora_pg_v18_lab.events (
  id,schema_version,event_type,timestamp,source,actor,confidence,
  importance,priority,payload_json,privacy_scope,dedupe_key
) VALUES (
  %s,1,%s,%s,'synthetic:connection-lab',%s,1.0,
  0.5,0,%s,'PERSONAL',%s
) ON CONFLICT (dedupe_key) DO NOTHING
""".strip()
FETCH_EVENT = """
SELECT id,event_type,timestamp,actor
FROM amora_pg_v18_lab.events
WHERE id=%s AND actor=%s
  AND privacy_scope NOT IN ('AUTH','SECRET')
""".strip()
COUNT_EVENTS = """
SELECT COUNT(*) AS total FROM amora_pg_v18_lab.events
WHERE actor=%s AND privacy_scope NOT IN ('AUTH','SECRET')
""".strip()

_ALLOWLIST = frozenset((COUNT_ACTIVE, FETCH_ALLOWED,
                        INSERT_EVENT, FETCH_EVENT, COUNT_EVENTS))
_BATCH_ALLOWLIST = frozenset((INSERT_EVENT,))
# This list is deliberately small and explicit; no SQL parsing or regex-based
# rewriting can expand the permissions.
_MAX_RESULT_ROWS = 1000


class LabConnectionError(ValueError):
    """Fixed error codes; no SQL parameter values appear in messages."""


class LabCommitOutcomeUnknown(RuntimeError):
    """Commit/rollback might not have completed; never retry a write."""


@dataclass(frozen=True)
class LabRow(Mapping[str, Any]):
    """Compatible with both SQLite row[0] and row['column'] / dict(row)."""

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


class LabResult:
    """Small materialized result, safe to access after transaction commit."""

    def __init__(self, *, columns: tuple[str, ...], values: list[tuple[Any, ...]],
                 rowcount: int):
        self._columns = columns
        self._rows = [LabRow(columns, tuple(row)) for row in values]
        self.rowcount = rowcount
        self._offset = 0

    def fetchone(self) -> LabRow | None:
        if self._offset >= len(self._rows):
            return None
        result = self._rows[self._offset]
        self._offset += 1
        return result

    def fetchall(self) -> list[LabRow]:
        result = self._rows[self._offset:]
        self._offset = len(self._rows)
        return result

    def __iter__(self) -> Iterator[LabRow]:
        while (row := self.fetchone()) is not None:
            yield row


class PostgresLabConnection:
    """Restricted implementation of the SQLConnection/QueryResult surface.

    Deliberately NOT registered in backend_from_environment and has no
    connect(path)/backup method. A real PG adapter needs many more contracts.
    """

    capabilities = StorageCapabilities(
        name="postgres-lab", local_wal=False,
        consistent_snapshot=False, remote=True,
    )

    def __init__(self, raw):
        # Fail before any SQL, even for a fake remote connection.
        ensure_local_lab(raw)
        if getattr(raw, "closed", True) or getattr(raw, "autocommit", False) is not True:
            raise LabConnectionError("PG_LAB_AUTOCOMMIT_REQUIRED")
        self._raw = raw
        self._contexts: list[Any] = []
        self._closed = False
        self._broken = False

    @property
    def in_transaction(self) -> bool:
        return bool(self._contexts)

    def _ensure_ready(self) -> None:
        if self._closed or self._broken:
            raise LabConnectionError("PG_LAB_CONNECTION_UNUSABLE")
        ensure_local_lab(self._raw)

    @staticmethod
    def _params(values: Iterable[Any]) -> tuple[Any, ...]:
        if isinstance(values, (str, bytes, bytearray, dict)):
            raise LabConnectionError("PG_LAB_POSITIONAL_PARAMETERS_REQUIRED")
        try:
            params = tuple(values)
        except TypeError as exc:
            raise LabConnectionError("PG_LAB_POSITIONAL_PARAMETERS_REQUIRED") from exc
        if any(type(value) not in (str, int, float, bool, bytes, type(None))
               for value in params):
            raise LabConnectionError("PG_LAB_UNSUPPORTED_PARAMETER_TYPE")
        # Preserve SQLite integer flags (bool values are stored as 0 or 1).
        return tuple(int(value) if type(value) is bool else value for value in params)

    @staticmethod
    def _statement(sql: str, allowed: frozenset[str] = _ALLOWLIST) -> None:
        if not isinstance(sql, str) or sql.strip() not in allowed:
            raise LabConnectionError("PG_LAB_SQL_NOT_REVIEWED")

    @staticmethod
    def _materialize(cursor) -> LabResult:
        rowcount = cursor.rowcount
        if cursor.description is None:
            return LabResult(columns=(), values=[], rowcount=rowcount)
        names = tuple(column.name for column in cursor.description)
        if len(set(names)) != len(names):
            raise LabConnectionError("PG_LAB_DUPLICATE_RESULT_COLUMNS")
        # Approved SELECTs return at most 1000 rows. No unbounded fetches
        # or silent truncation if a future query is accidentally added.
        values = cursor.fetchmany(_MAX_RESULT_ROWS + 1)
        if len(values) > _MAX_RESULT_ROWS:
            raise LabConnectionError("PG_LAB_RESULT_LIMIT_EXCEEDED")
        return LabResult(columns=names, values=list(values), rowcount=rowcount)

    def execute(self, sql: str, parameters: Iterable[Any] = ()) -> LabResult:
        self._ensure_ready()
        self._statement(sql)
        params = self._params(parameters)
        with self._raw.cursor() as cursor:
            cursor.execute(sql, params)
            return self._materialize(cursor)

    def executemany(self, sql: str, values: Iterable[Iterable[Any]]) -> int:
        self._ensure_ready()
        self._statement(sql, _BATCH_ALLOWLIST)
        if not self.in_transaction:
            raise LabConnectionError("PG_LAB_BATCH_REQUIRES_TRANSACTION")
        try:
            params = [self._params(row) for row in values]
        except TypeError as exc:
            raise LabConnectionError("PG_LAB_BATCH_PARAMETERS_REQUIRED") from exc
        if len(params) > 256:
            raise LabConnectionError("PG_LAB_BATCH_TOO_LARGE")
        with self._raw.cursor() as cursor:
            cursor.executemany(sql, params)
            return cursor.rowcount

    def executescript(self, _script: str):
        raise LabConnectionError("PG_LAB_SQLITE_SCRIPT_UNSUPPORTED")

    def __enter__(self):
        self._ensure_ready()
        transaction = self._raw.transaction()
        try:
            transaction.__enter__()
        except BaseException:
            self._broken = True
            self._raw.close()
            raise
        self._contexts.append(transaction)
        return self

    def __exit__(self, kind, value, traceback):
        if not self._contexts:
            raise LabConnectionError("PG_LAB_TRANSACTION_NOT_OPEN")
        transaction = self._contexts.pop()
        try:
            return transaction.__exit__(kind, value, traceback)
        except BaseException as exc:
            # If COMMIT lost its connection, its outcome is ambiguous.
            # No reconnect/replay: leave operator-level reconciliation.
            self._broken = True
            try:
                self._raw.close()
            finally:
                raise LabCommitOutcomeUnknown("PG_LAB_COMMIT_OR_ROLLBACK_UNKNOWN") from exc

    def close(self) -> None:
        if not self._closed:
            self._closed = True
            self._raw.close()
