"""Read-only Run 3 readiness inspection for a prepared PostgreSQL 18 target.

No migrations, writes, network configuration, credential loading or cutover.
The caller explicitly owns a connected psycopg connection and an exact target
policy. A structural pass is NOT authorization to serve production traffic.
"""
from __future__ import annotations

from dataclasses import dataclass
from collections import Counter

from .postgres_v18_lab_schema import _checks
from .postgres_runtime_connection import PostgresTargetPolicy, PostgresRuntimeError
from .postgres_v18_manifest import build_manifest

_REQUIRED_RUNTIME_TABLES = frozenset((
    "memory_search", "fts_doc_tokens", "fts_term_postings",
    "speech_turn_stops",
))
_REQUIRED_FTS_INDEXES = frozenset(("fts_term_postings_lookup",))


@dataclass(frozen=True, slots=True)
class Run3PreflightResult:
    status: str
    checks: tuple[str, ...]
    blockers: tuple[str, ...]
    schema_version: int | None = None
    relational_tables: int | None = None
    # Structural checks cannot attest Aiven backups, private import, transport
    # or rollout approval. This MUST stay False, including for localhost PASS.
    production_ready: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "status": self.status,
            "checks": list(self.checks),
            "blockers": list(self.blockers),
            "schema_version": self.schema_version,
            "relational_tables": self.relational_tables,
            "production_ready": False,
        }


def inspect_prepared_postgres(
    raw, target: PostgresTargetPolicy, *, require_memory_epoch: bool = True,
) -> Run3PreflightResult:
    """Inspect an already prepared schema inside a REPEATABLE READ READ ONLY tx.

    Caller-provided `raw` is never closed; all reads are scoped to its exact
    verified destination. No host, query results, row data or credentials are
    returned in the report. Errors fail closed with stable diagnostic codes.
    """
    checks: list[str] = []
    blockers: list[str] = []
    schema_version: int | None = None
    table_count: int | None = None
    if not isinstance(target, PostgresTargetPolicy):
        return Run3PreflightResult("blocked", (), ("PG_PREFLIGHT_INVALID_TARGET",))
    try:
        target.verify(raw)
    except (PostgresRuntimeError, AttributeError, TypeError, ValueError):
        return Run3PreflightResult("blocked", (), ("PG_PREFLIGHT_TARGET_NOT_VERIFIED",))
    if getattr(raw, "closed", True) or getattr(raw, "autocommit", None) is not True:
        return Run3PreflightResult("blocked", (), ("PG_PREFLIGHT_AUTOCOMMIT_REQUIRED",))

    manifest = build_manifest()  # recreated from canonical migrations in memory
    expected = {
        table["name"]: {
            col["name"]: col["postgres_type"] for col in table["columns"]
        }
        for table in manifest["tables"] if table["kind"] == "ordinary"
    }
    expected["memory_search"] = {
        "memory_id": "text", "content": "text", "tags": "text",
    }
    required = set(expected) | _REQUIRED_RUNTIME_TABLES
    schema = target.schema  # policy validates identifiers, never caller SQL
    try:
        with raw.transaction():
            raw.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY")
            read_only = raw.execute(
                "SELECT current_setting('transaction_read_only')"
            ).fetchone()
            if read_only is None or read_only[0] != "on":
                blockers.append("PG_PREFLIGHT_NOT_READ_ONLY")
            else:
                checks.append("read_only_snapshot")

            tables = {
                row[0] for row in raw.execute(
                    "SELECT tablename FROM pg_catalog.pg_tables "
                    "WHERE schemaname=%s", (schema,),
                ).fetchall()
            }
            table_count = len(tables)
            if not required.issubset(tables):
                blockers.append("PG_PREFLIGHT_TABLES_MISSING")
            else:
                checks.append("required_relations")

            column_rows = raw.execute(
                "SELECT table_name,column_name,data_type "
                "FROM information_schema.columns "
                "WHERE table_schema=%s "
                "ORDER BY table_name,ordinal_position", (schema,),
            ).fetchall()
            actual: dict[str, dict[str, str]] = {}
            for table, column, data_type in column_rows:
                actual.setdefault(table, {})[column] = data_type
            if any(
                actual.get(table) != columns for table, columns in expected.items()
            ):
                blockers.append("PG_PREFLIGHT_COLUMN_TYPE_DRIFT")
            else:
                checks.append("canonical_columns_and_types")

            indexes = {
                row[0] for row in raw.execute(
                    "SELECT indexname FROM pg_catalog.pg_indexes "
                    "WHERE schemaname=%s", (schema,),
                ).fetchall()
            }
            if not _REQUIRED_FTS_INDEXES.issubset(indexes):
                blockers.append("PG_PREFLIGHT_FTS_INDEX_MISSING")
            else:
                checks.append("fts_lookup_index")
            # All canonical explicit indexes must exist, not just the FTS
            # lookup. A valid schema can still have silently missing indexes.
            canonical_indexes = {item["name"] for item in manifest["indexes"]}
            canonical_indexes.add("amora_v18_memory_search_gin")
            if not canonical_indexes.issubset(indexes):
                blockers.append("PG_PREFLIGHT_CANONICAL_INDEX_MISSING")
            else:
                checks.append("canonical_indexes")


            # Validate *presence* as well as validation state. Counting only
            # unvalidated constraints would accept a database with every FK
            # silently removed (zero remaining constraints is not safe).
            constraint_rows = raw.execute(
                "SELECT t.relname,c.conname,c.contype,c.convalidated "
                "FROM pg_catalog.pg_constraint c "
                "JOIN pg_catalog.pg_class t ON t.oid=c.conrelid "
                "JOIN pg_catalog.pg_namespace n ON n.oid=t.relnamespace "
                "WHERE n.nspname=%s AND c.contype IN ('f','c')",
                (schema,),
            ).fetchall()
            invalid = any(not row[3] for row in constraint_rows)
            if invalid:
                blockers.append("PG_PREFLIGHT_CONSTRAINTS_UNVALIDATED")
            else:
                checks.append("validated_constraints")
            actual_fks = {
                (table, name) for table, name, kind, _ in constraint_rows
                if kind == "f"
            }
            expected_fks = {
                (table["name"], f"fk_{table['name']}_{ordinal}")
                for table in manifest["tables"]
                if table["kind"] == "ordinary"
                for ordinal, _ in enumerate(table["foreign_keys"])
            }
            if not expected_fks.issubset(actual_fks):
                blockers.append("PG_PREFLIGHT_FOREIGN_KEY_MISSING")
            else:
                checks.append("canonical_foreign_keys")
            actual_checks = Counter(
                table for table, _, kind, _ in constraint_rows if kind == "c"
            )
            expected_checks = {
                table["name"]: len(_checks(table["sqlite_create_sql"]))
                for table in manifest["tables"]
                if table["kind"] == "ordinary"
            }
            if any(
                actual_checks[name] < minimum
                for name, minimum in expected_checks.items()
            ):
                blockers.append("PG_PREFLIGHT_CHECK_CONSTRAINT_MISSING")
            else:
                checks.append("canonical_check_constraints")

            if "schema_migrations" in tables:
                # The sole identifier interpolation is a *validated* policy
                # schema, never input from connection text or credentials.
                versions = [
                    row[0] for row in raw.execute(
                        'SELECT version FROM "' + schema
                        + '".schema_migrations ORDER BY version'
                    ).fetchall()
                ]
                if versions != list(range(1, 19)):
                    blockers.append("PG_PREFLIGHT_MIGRATION_VERSION_DRIFT")
                else:
                    schema_version = 18
                    checks.append("canonical_v18_migrations")
            else:
                blockers.append("PG_PREFLIGHT_MIGRATION_TABLE_MISSING")

            if require_memory_epoch:
                if "amora_memory_projection_epoch" not in tables:
                    blockers.append("PG_PREFLIGHT_EPOCH_NOT_INSTALLED")
                else:
                    epoch = raw.execute(
                        'SELECT epoch_id,revision FROM "' + schema
                        + '".amora_memory_projection_epoch WHERE singleton=1'
                    ).fetchall()
                    if (len(epoch) != 1 or not isinstance(epoch[0][0], str)
                            or not epoch[0][0] or type(epoch[0][1]) is not int
                            or epoch[0][1] < 0):
                        blockers.append("PG_PREFLIGHT_EPOCH_INVALID")
                    else:
                        checks.append("memory_epoch_present")
    except Exception:
        # psycopg error text can contain destination, query or credential
        # details. Report only stable codes and no sensitive results.
        blockers.append("PG_PREFLIGHT_READ_FAILED")

    return Run3PreflightResult(
        "structural_pass" if not blockers else "blocked",
        tuple(checks), tuple(dict.fromkeys(blockers)),
        schema_version=schema_version,
        relational_tables=table_count,
    )
