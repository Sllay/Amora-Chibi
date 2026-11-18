"""Operator-injected, one-shot EMPTY PG18 staging schema preparation.

No DSN/env/secret access, no source snapshot reads, no automatic Northflank
entrypoint or Core backend switch. The target connection must be explicitly
pinned, isolated from any other application data, and writable only to this
empty schema. The newly created schema is NOT ready for serving: no private
data has been imported and schema_migrations remains empty.

Schema DDL is compiled from canonical v18 metadata plus shared reviewed
BM25, speech-stop and memory-epoch definitions used in disposable PG18 CI.
"""
from __future__ import annotations

from uuid import uuid4

from .postgres_v18_lab_schema import LAB_SCHEMA, pg_ddl_statements
from .postgres_fts_bm25_lab import bm25_schema_ddl
from .postgres_voice_stop_lab import speech_stop_schema_ddl
from .postgres_memory_epoch_lab import memory_epoch_schema_statements
from .postgres_runtime_connection import PostgresTargetPolicy
from .postgres_run3_preflight import inspect_prepared_postgres

_CONFIRM = "PREPARE_EMPTY_PG18_ISOLATED_STAGING_ONLY"
_LOCAL_HOSTS = frozenset(("127.0.0.1", "localhost", "::1"))


class StagingSchemaError(RuntimeError):
    """Fixed non-sensitive reason codes only."""


def _require_target(raw, target: PostgresTargetPolicy, confirmation: str) -> None:
    # Refuse all unintended calls *before* any database interaction.
    if confirmation != _CONFIRM:
        raise StagingSchemaError("PG_STAGING_OPERATOR_CONFIRMATION_REQUIRED")
    if not isinstance(target, PostgresTargetPolicy) or target.schema != LAB_SCHEMA:
        raise StagingSchemaError("PG_STAGING_SCHEMA_POLICY_MISMATCH")
    host = target.host.lower()
    if host not in _LOCAL_HOSTS and (
        not target.require_tls or not host.endswith(".aivencloud.com")
    ):
        raise StagingSchemaError("PG_STAGING_REMOTE_TARGET_NOT_AIVEN_TLS")
    try:
        target.verify(raw)
        if getattr(raw, "autocommit", None) is not True:
            raise StagingSchemaError("PG_STAGING_AUTOCOMMIT_REQUIRED")
    except StagingSchemaError:
        raise
    except Exception:
        raise StagingSchemaError("PG_STAGING_TARGET_NOT_VERIFIED") from None


def _assert_empty_database(raw) -> None:
    # Read only pg_catalog metadata. Refuse any existing staging namespace,
    # even if it looks empty; never CREATE IF NOT EXISTS or replace objects.
    if raw.execute(
        "SELECT COUNT(*) FROM pg_catalog.pg_namespace WHERE nspname=%s",
        (LAB_SCHEMA,),
    ).fetchone()[0] != 0:
        raise StagingSchemaError("PG_STAGING_SCHEMA_ALREADY_EXISTS")
    if raw.execute(
        "SELECT COUNT(*) FROM pg_catalog.pg_tables "
        "WHERE schemaname NOT IN ('pg_catalog','information_schema') "
        "AND schemaname NOT LIKE 'pg_toast%'"
    ).fetchone()[0] != 0:
        raise StagingSchemaError("PG_STAGING_DATABASE_NOT_ISOLATED")


def prepare_empty_staging_schema(
    raw, target: PostgresTargetPolicy, *, confirmation: str = "",
) -> dict[str, object]:
    """Prepare the reviewed schema once and certify only PRE-IMPORT structure.

    The caller must not retry after any ambiguous connection or COMMIT error.
    The schema is created in one transaction, but other clients might have
    connected concurrently: an operator must independently ensure isolation.
    """
    _require_target(raw, target, confirmation)
    try:
        _assert_empty_database(raw)
    except StagingSchemaError:
        raise
    except Exception:
        raise StagingSchemaError("PG_STAGING_EMPTY_CHECK_FAILED") from None

    try:
        canonical_ddl, summary = pg_ddl_statements()
        epoch_id = str(uuid4())
        auxiliary_ddl = (*bm25_schema_ddl(), speech_stop_schema_ddl())
        epoch_ddl = memory_epoch_schema_statements(epoch_id)
    except Exception:
        raise StagingSchemaError("PG_STAGING_DDL_BUILD_FAILED") from None

    try:
        with raw.transaction():
            # Bound lock/statement duration on a low-budget Free database.
            raw.execute("SET LOCAL lock_timeout = '5s'")
            raw.execute("SET LOCAL statement_timeout = '60s'")
            _assert_empty_database(raw)
            for statement in (*canonical_ddl, *auxiliary_ddl):
                raw.execute(statement)
            for statement, parameters in epoch_ddl:
                if parameters:
                    raw.execute(statement, parameters)
                else:
                    raw.execute(statement)
    except StagingSchemaError:
        raise
    except Exception:
        raise StagingSchemaError("PG_STAGING_PREPARATION_FAILED_REVIEW_DESTINATION") from None

    # Validate in an independent read-only snapshot AFTER COMMIT.
    # An unknown failure here does not authorize repeated DDL.
    check = inspect_prepared_postgres(
        raw, target, require_memory_epoch=True,
        allow_empty_migrations_for_import=True,
    )
    if check.status != "preimport_structural_pass":
        raise StagingSchemaError("PG_STAGING_POSTCOMMIT_PREFLIGHT_BLOCKED")

    return {
        "status": "empty_staging_schema_prepared",
        "schema_version_target": 18,
        "relational_tables": summary["postgres_source_tables"],
        "source_tables": summary["source_tables"],
        "canonical_indexes": summary["source_indexes"],
        "auxiliary_tables_prepared": 4,
        "postcommit_readonly_preflight": "preimport_structural_pass",
        "source_data_read": False,
        "private_import_performed": False,
        "source_freshness_certified": False,
        "external_backup_and_rollback_tested": False,
        "production_cutover_authorized": False,
    }
