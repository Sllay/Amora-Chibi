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
import re
from threading import Lock, RLock, get_ident
from typing import Any, Callable


class PostgresRuntimeError(RuntimeError):
    """Safe stable error codes: never include SQL, parameters or credentials."""


class PostgresCommitUnknown(PostgresRuntimeError):
    """Commit outcome may be ambiguous; a failed write must not be replayed."""



@dataclass(frozen=True)
class PostgresTargetPolicy:
    """Pinned connection destination, including TLS requirements for Aiven.

    No credentials are held or logged. Supplying a remote policy does not
    register the backend in the production environment selector.
    """

    host: str = "127.0.0.1"
    database: str = "amora_pg_synthetic"
    schema: str = "amora_pg_v18_lab"
    port: int = 5432
    server_major: int = 18
    require_tls: bool = False

    def __post_init__(self) -> None:
        if (not isinstance(self.host, str)
                or not self.host or any(char in self.host for char in "/@?# ")
                or not isinstance(self.database, str)
                or not self.database or any(char in self.database for char in "/@?# ")
                or not isinstance(self.schema, str)
                or re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", self.schema) is None
                or type(self.port) is not int or not 1 <= self.port <= 65535
                or type(self.server_major) is not int or self.server_major != 18
                or type(self.require_tls) is not bool):
            raise ValueError("PG_RUNTIME_INVALID_TARGET")
        if self.host not in {"127.0.0.1", "::1", "localhost"} and not self.require_tls:
            raise ValueError("PG_RUNTIME_REMOTE_TLS_REQUIRED")

    def verify(self, raw: Any) -> None:
        """Check effective libpq destination, not merely caller-supplied DSN."""
        info = getattr(raw, "info", None)
        try:
            matching = (
                info is not None
                and info.host == self.host
                and info.dbname == self.database
                and int(info.port) == self.port
                and int(info.server_version) // 10000 == self.server_major
            )
        except (AttributeError, TypeError, ValueError):
            matching = False
        if not matching:
            raise PostgresRuntimeError("PG_RUNTIME_DESTINATION_MISMATCH")
        if self.require_tls:
            parameters = (
                info.get_parameters()
                if callable(getattr(info, "get_parameters", None)) else {}
            )
            pgconn = getattr(raw, "pgconn", None)
            if (parameters.get("sslmode") != "verify-full"
                    or getattr(pgconn, "ssl_in_use", False) is not True):
                raise PostgresRuntimeError("PG_RUNTIME_TLS_NOT_VERIFIED")


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


# Schema-qualified translations for Core bootstrap metadata. Schema names
# come only from the validated PostgresTargetPolicy identifier, never SQL input.
def _infra_sql(schema: str) -> dict[str, ReviewedStatement]:
    table = '"' + schema + '".schema_migrations'
    return {
        "SELECT version FROM schema_migrations": ReviewedStatement(
            "SELECT version FROM " + table, 0, 32,
        ),
        "SELECT COALESCE(MAX(version), 0) FROM schema_migrations": ReviewedStatement(
            "SELECT COALESCE(MAX(version), 0) FROM " + table, 0, 1,
        ),
    }


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
        *, target: PostgresTargetPolicy | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        if target is not None and not isinstance(target, PostgresTargetPolicy):
            raise PostgresRuntimeError("PG_RUNTIME_INVALID_TARGET_POLICY")
        self._target = target or PostgresTargetPolicy()
        self._target.verify(raw)
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
        self._state_lock = RLock()
        self._transaction_owner: int | None = None
        self._closed = False
        self._broken = False
        self._on_close = on_close

    @property
    def in_transaction(self) -> bool:
        with self._state_lock:
            return bool(self._transactions)

    def _ensure_ready(self) -> None:
        if self._closed or self._broken or getattr(self._raw, "closed", True):
            raise PostgresRuntimeError("PG_RUNTIME_CONNECTION_UNUSABLE")
        self._target.verify(self._raw)

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
        with self._state_lock:
            self._ensure_ready()
            statement = self._review(sql)
            params = self._parameters(parameters, statement.parameter_count)
            with self._raw.cursor() as cursor:
                cursor.execute(statement.postgres_sql, params)
                return self._materialize(cursor, statement.max_result_rows)

    def executemany(
        self, sql: str, values: Iterable[Iterable[Any]],
    ) -> int:
        with self._state_lock:
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
        # Keep the reentrant lock for the full with-block. No other thread
        # can write into a transaction owned by the current thread.
        self._state_lock.acquire()
        try:
            self._ensure_ready()
            transaction = self._raw.transaction()
            try:
                transaction.__enter__()
            except BaseException:
                self._broken = True
                try:
                    self.close()
                except Exception:
                    pass
                raise
            self._transactions.append(transaction)
            self._transaction_owner = get_ident()
            return self
        except BaseException:
            self._state_lock.release()
            raise

    def __exit__(self, kind, value, traceback):
        if not self._transactions or self._transaction_owner != get_ident():
            raise PostgresRuntimeError("PG_RUNTIME_TRANSACTION_NOT_OPEN")
        transaction = self._transactions.pop()
        try:
            return transaction.__exit__(kind, value, traceback)
        except BaseException as exc:
            self._broken = True
            try:
                self.close()
            except Exception:
                pass
            raise PostgresCommitUnknown("PG_RUNTIME_COMMIT_OR_ROLLBACK_UNKNOWN") from exc
        finally:
            if not self._transactions:
                self._transaction_owner = None
            self._state_lock.release()

    def verify_prepared_schema(self, expected_version: int) -> None:
        """Verify an already-imported synthetic v18 schema; never apply DDL.

        This is an infrastructure guard. It does not certify SQL/ranking
        parity or permit serving against real Aiven records.
        """
        with self._state_lock:
            self._ensure_ready()
            if expected_version != 18:
                raise PostgresRuntimeError("PG_RUNTIME_SCHEMA_TARGET_UNSUPPORTED")
            from .postgres_v18_manifest import build_manifest

            manifest = build_manifest()
            ordinary = [
                table for table in manifest["tables"]
                if table["kind"] == "ordinary"
            ]
            required = {table["name"] for table in ordinary} | {"memory_search"}
            schema = self._target.schema
            expected_columns = {
                table["name"]: tuple(column["name"] for column in table["columns"])
                for table in ordinary
            }
            expected_columns["memory_search"] = ("memory_id", "content", "tags")
            try:
                with self._raw.cursor() as cursor:
                    cursor.execute(
                        "SELECT tablename FROM pg_catalog.pg_tables "
                        "WHERE schemaname=%s", (schema,),
                    )
                    actual = {row[0] for row in cursor.fetchall()}
                    if not required.issubset(actual):
                        raise PostgresRuntimeError("PG_RUNTIME_SCHEMA_TABLES_MISSING")
                    cursor.execute(
                        "SELECT table_name, column_name "
                        "FROM information_schema.columns "
                        "WHERE table_schema=%s "
                        "ORDER BY table_name, ordinal_position",
                        (schema,),
                    )
                    actual_columns: dict[str, list[str]] = {}
                    for table_name, column_name in cursor.fetchall():
                        actual_columns.setdefault(table_name, []).append(column_name)
                    if any(tuple(actual_columns.get(name, ())) != columns
                           for name, columns in expected_columns.items()):
                        raise PostgresRuntimeError("PG_RUNTIME_SCHEMA_COLUMNS_MISMATCH")
                    cursor.execute(
                        'SELECT version FROM "' + schema + '".schema_migrations '
                        "ORDER BY version"
                    )
                    versions = tuple(row[0] for row in cursor.fetchall())
                if versions != tuple(range(1, expected_version + 1)):
                    raise PostgresRuntimeError("PG_RUNTIME_SCHEMA_VERSION_MISMATCH")
            except PostgresRuntimeError:
                raise
            except Exception as exc:
                raise PostgresRuntimeError("PG_RUNTIME_SCHEMA_NOT_PREPARED") from exc

    def close(self) -> None:
        with self._state_lock:
            if not self._closed:
                self._closed = True
                try:
                    self._raw.close()
                finally:
                    if self._on_close is not None:
                        self._on_close()


class PostgresBackendCandidate:
    """StorageBackend-compatible factory; deliberately not environment-selectable.

    An embedding application must supply an already-reviewed psycopg connection
    factory and the complete SQL statement registry. No production cutover,
    schema creation or backup is inferred from constructing this object.
    """

    def __init__(
        self, connection_factory: Any,
        statements: Mapping[str, ReviewedStatement],
        *, target: PostgresTargetPolicy | None = None,
        max_connections: int = 16,
    ) -> None:
        from .backend import StorageCapabilities

        if not callable(connection_factory):
            raise PostgresRuntimeError("PG_RUNTIME_FACTORY_REQUIRED")
        self.capabilities = StorageCapabilities(
            name="postgres-candidate",
            local_wal=False,
            consistent_snapshot=False,
            remote=True,
        )
        if type(max_connections) is not int or not 1 <= max_connections <= 16:
            raise PostgresRuntimeError("PG_RUNTIME_INVALID_CONNECTION_BUDGET")
        if target is not None and not isinstance(target, PostgresTargetPolicy):
            raise PostgresRuntimeError("PG_RUNTIME_INVALID_TARGET_POLICY")
        self._factory = connection_factory
        self._target = target or PostgresTargetPolicy()
        self._statements = _infra_sql(self._target.schema)
        for sqlite_sql, statement in statements.items():
            if sqlite_sql in self._statements and self._statements[sqlite_sql] != statement:
                raise PostgresRuntimeError("PG_RUNTIME_INFRA_SQL_CONFLICT")
            self._statements[sqlite_sql] = statement
        self._max_connections = max_connections
        self._opened = 0
        self._lock = Lock()
        if not self._statements:
            raise PostgresRuntimeError("PG_RUNTIME_NO_REVIEWED_SQL")

    def connect(self, _path: Any) -> PostgresRuntimeConnection:
        # The existing storage protocol carries a SQLite file path; a PG
        # connection is selected by the explicit factory, not by that path.
        with self._lock:
            if self._opened >= self._max_connections:
                raise PostgresRuntimeError("PG_RUNTIME_CONNECTION_BUDGET_EXCEEDED")
            self._opened += 1
        try:
            raw = self._factory()
        except BaseException:
            self._release()
            raise
        try:
            return PostgresRuntimeConnection(
                raw, self._statements, target=self._target,
                on_close=self._release,
            )
        except BaseException:
            try:
                raw.close()
            except Exception:
                pass
            finally:
                self._release()
            raise

    @property
    def opened_connections(self) -> int:
        with self._lock:
            return self._opened

    def _release(self) -> None:
        with self._lock:
            self._opened -= 1

    def backup(self, _connection: Any, _destination: Any):
        # SQLite's file-based backup contract cannot represent PostgreSQL
        # snapshot/restore guarantees. Never fake a successful backup.
        raise PostgresRuntimeError("PG_RUNTIME_BACKUP_NOT_IMPLEMENTED")


def make_psycopg_connection_factory(
    *, target: PostgresTargetPolicy, username: str, password: str,
    sslrootcert: str | None = None, connect_timeout: int = 5,
) -> Callable[[], Any]:
    """Explicit psycopg3 connection factory; NEVER selected by environment.

    Production credentials are supplied by the caller at runtime. Remote
    destinations must use verify-full with an explicit CA certificate.
    This function does not open any connection until its result is called.
    """
    if not username or not password:
        raise PostgresRuntimeError("PG_RUNTIME_CREDENTIALS_REQUIRED")
    if type(connect_timeout) is not int or not 1 <= connect_timeout <= 30:
        raise PostgresRuntimeError("PG_RUNTIME_INVALID_TIMEOUT")
    if target.require_tls and not sslrootcert:
        raise PostgresRuntimeError("PG_RUNTIME_CA_REQUIRED")

    def connect() -> Any:
        try:
            import psycopg
            connection_args = dict(
                host=target.host,
                port=target.port,
                dbname=target.database,
                user=username,
                password=password,
                connect_timeout=connect_timeout,
                sslmode="verify-full" if target.require_tls else "disable",
                options="-c search_path=pg_catalog",
                autocommit=True,
            )
            if sslrootcert is not None:
                connection_args["sslrootcert"] = sslrootcert
            raw = psycopg.connect(**connection_args)
        except Exception:
            # libpq errors can contain hostnames, usernames or other sensitive
            # connection details. Keep the public exception code deterministic.
            raise PostgresRuntimeError("PG_RUNTIME_CONNECT_FAILED") from None
        try:
            target.verify(raw)
        except BaseException:
            raw.close()
            raise
        return raw

    return connect
