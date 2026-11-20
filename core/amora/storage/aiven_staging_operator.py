"""Explicitly gated Aiven PG18 EMPTY staging schema operator.

Never used by serving Core and never auto-runs. 'inspect' performs only PG
catalog queries in a read-only session. 'prepare' is a one-shot DDL action
that needs four separate operator confirmations plus a pinned verified
Aiven connection; it NEVER imports private data or changes Core routing.

Operator attestations are NOT technical certification of offsite backup,
freshness, isolation, free-tier capacity, rollback or production readiness.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from .aiven_preflight import (
    AivenPreflightError, load_aiven_ca_certificate, validate_aiven_settings,
)
from .postgres_runtime_connection import PostgresTargetPolicy
from .postgres_run3_preflight import inspect_prepared_postgres
from .postgres_staging_schema_prepare import (
    StagingSchemaError, prepare_empty_staging_schema,
)
from .postgres_v18_lab_schema import LAB_SCHEMA


class AivenStagingOperatorError(RuntimeError):
    """Fixed no-secrets reason codes only."""


_CONFIRM = "PREPARE_EMPTY_PG18_ISOLATED_STAGING_ONLY"
_MAX_DB_BYTES_FOR_REVIEW = 512 * 1024 * 1024
# This is only a conservative operator-review stop, NOT a disk capacity
# measurement and NOT proof that the full PCS.4C source will fit.


def _blocked(reason: str) -> dict[str, object]:
    return {
        "status": "blocked",
        "reason": reason,
        "aiven_import_performed": False,
        "core_storage_switched": False,
        "production_cutover_authorized": False,
    }


def inspect_or_prepare(
    *, mode: str, dsn: str, ca_pem: str, confirmation: str = "",
    snapshot_attested: bool = False,
    external_backup_restore_attested: bool = False,
    isolated_target_attested: bool = False,
    free_plan_capacity_attested: bool = False,
    expected_host: str = "",
    expected_port: int = 0,
    expected_database: str = "",
    connect=None,
) -> dict[str, object]:
    """A single bounded manual connection to the exact configured Aiven DB."""
    if mode not in {"inspect", "prepare", "verify"}:
        raise AivenStagingOperatorError("PG_STAGING_UNREVIEWED_MODE")
    if mode == "prepare" and (
        confirmation != _CONFIRM
        or not snapshot_attested
        or not external_backup_restore_attested
        or not isolated_target_attested
        or not free_plan_capacity_attested
    ):
        # Never even open a network socket without all human approvals.
        raise AivenStagingOperatorError("PG_STAGING_OPERATOR_GATES_REQUIRED")

    try:
        validate_aiven_settings(dsn, ca_pem)
        endpoint = urlsplit(dsn)
        host, port, database = endpoint.hostname, endpoint.port, endpoint.path[1:]
        if (not host or not port or not database or "/" in database
                or "%" in database or "\x00" in database):
            raise AivenStagingOperatorError("PG_STAGING_INVALID_ENDPOINT")
        if not expected_host or not expected_port or not expected_database:
            raise AivenStagingOperatorError("PG_STAGING_ENDPOINT_PIN_REQUIRED")
        if (host != expected_host or port != expected_port
                or database != expected_database):
            raise AivenStagingOperatorError("PG_STAGING_ENDPOINT_PIN_MISMATCH")
        policy = PostgresTargetPolicy(
            host=host, port=port, database=database,
            schema=LAB_SCHEMA, server_major=18, require_tls=True,
        )
    except AivenStagingOperatorError:
        raise
    except (AivenPreflightError, ValueError, TypeError):
        raise AivenStagingOperatorError("PG_STAGING_CONFIGURATION_INVALID") from None

    if connect is None:
        try:
            import psycopg
        except ImportError:
            raise AivenStagingOperatorError("PG_STAGING_DRIVER_UNAVAILABLE") from None
        connect = psycopg.connect

    with tempfile.TemporaryDirectory(prefix="amora-staging-ca-") as folder:
        ca_path = Path(folder) / "ca.pem"
        fd = os.open(str(ca_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as out:
            out.write(ca_pem)
        try:
            raw = connect(
                dsn,
                sslmode="verify-full",
                sslrootcert=str(ca_path),
                connect_timeout=8,
                application_name="amora-staging-schema-operator",
                options=("-c default_transaction_read_only=on"
                         if mode in {"inspect", "verify"}
                         else "-c default_transaction_read_only=off"),
                autocommit=True,
            )
        except Exception:
            raise AivenStagingOperatorError("PG_STAGING_CONNECTION_FAILED") from None

        with closing(raw):
            try:
                policy.verify(raw)
            except Exception:
                raise AivenStagingOperatorError("PG_STAGING_TARGET_NOT_VERIFIED") from None

            try:
                exists = int(raw.execute(
                    "SELECT COUNT(*) FROM pg_catalog.pg_namespace WHERE nspname=%s",
                    (LAB_SCHEMA,),
                ).fetchone()[0])
                user_tables = int(raw.execute(
                    "SELECT COUNT(*) FROM pg_catalog.pg_tables "
                    "WHERE schemaname NOT IN ('pg_catalog','information_schema') "
                    "AND schemaname NOT LIKE 'pg_toast%'",
                ).fetchone()[0])
                database_bytes = int(raw.execute(
                    "SELECT pg_catalog.pg_database_size(current_database())",
                ).fetchone()[0])
            except Exception:
                raise AivenStagingOperatorError("PG_STAGING_METADATA_QUERY_FAILED") from None

            if mode == "verify":
                # A post-DDL audit, including after an uncertain write exit.
                # This is read-only and never repeats schema creation.
                try:
                    checked = inspect_prepared_postgres(
                        raw, policy, require_memory_epoch=True,
                        allow_empty_migrations_for_import=True,
                    )
                except Exception:
                    raise AivenStagingOperatorError(
                        "PG_STAGING_READONLY_VERIFY_FAILED"
                    ) from None
                if checked.status not in {
                    "preimport_structural_pass", "structural_pass",
                }:
                    raise AivenStagingOperatorError(
                        "PG_STAGING_STRUCTURE_NOT_VERIFIED"
                    )
                return {
                    "status": "staging_structure_verified",
                    "operation": "read_only_structural_verify",
                    "structural_state": checked.status,
                    "schema_version_target": 18,
                    "database_user_tables": user_tables,
                    "database_size_bytes": database_bytes,
                    "capacity_independently_verified": False,
                    "private_row_parity_independently_verified": False,
                    "external_backup_and_restore_independently_verified": False,
                    "aiven_import_performed": False,
                    "core_storage_switched": False,
                    "production_cutover_authorized": False,
                }

            if exists or user_tables:
                raise AivenStagingOperatorError("PG_STAGING_DESTINATION_NOT_EMPTY")
            if database_bytes > _MAX_DB_BYTES_FOR_REVIEW:
                raise AivenStagingOperatorError("PG_STAGING_CAPACITY_REVIEW_REQUIRED")

            if mode == "inspect":
                return {
                    "status": "staging_preparation_possible",
                    "server_major": 18,
                    "staging_schema_exists": False,
                    "database_user_tables": user_tables,
                    "database_size_bytes": database_bytes,
                    "operation": "read_only_inspect",
                    "capacity_independently_verified": False,
                    "external_backup_and_restore_independently_verified": False,
                    "aiven_import_performed": False,
                    "core_storage_switched": False,
                    "production_cutover_authorized": False,
                }

            try:
                result = prepare_empty_staging_schema(
                    raw, policy, confirmation=_CONFIRM,
                )
            except StagingSchemaError as exc:
                # Stable operator-only error codes are sanitized by callee.
                raise AivenStagingOperatorError(str(exc)) from None
            except Exception:
                raise AivenStagingOperatorError(
                    "PG_STAGING_PREPARATION_FAILED_REVIEW_DESTINATION"
                ) from None
            return {
                **result,
                "operation": "one_shot_preparation",
                "operator_attested_backup_restore": external_backup_restore_attested,
                "operator_attested_source_snapshot": snapshot_attested,
                "operator_attested_isolation": isolated_target_attested,
                "operator_attested_free_plan_capacity": free_plan_capacity_attested,
                "capacity_independently_verified": False,
                "aiven_import_performed": False,
                "core_storage_switched": False,
                "production_cutover_authorized": False,
            }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "prepare", "verify"))
    parser.add_argument("--confirm", default="")
    parser.add_argument("--snapshot-attested", action="store_true")
    parser.add_argument("--external-backup-restore-attested", action="store_true")
    parser.add_argument("--isolated-target-attested", action="store_true")
    parser.add_argument("--free-plan-capacity-attested", action="store_true")
    args = parser.parse_args(argv)

    try:
        ca = load_aiven_ca_certificate(
            pem=os.environ.get("AMORA_POSTGRES_CA_PEM", ""),
            file_path=os.environ.get("AMORA_POSTGRES_CA_FILE", ""),
        )
        result = inspect_or_prepare(
            mode=args.mode,
            dsn=os.environ.get("AMORA_POSTGRES_DSN", ""),
            ca_pem=ca,
            confirmation=args.confirm,
            snapshot_attested=args.snapshot_attested,
            external_backup_restore_attested=args.external_backup_restore_attested,
            isolated_target_attested=args.isolated_target_attested,
            free_plan_capacity_attested=args.free_plan_capacity_attested,
            expected_host=os.environ.get("AMORA_STAGING_EXPECTED_HOST", ""),
            expected_port=int(os.environ.get("AMORA_STAGING_EXPECTED_PORT", "0")),
            expected_database=os.environ.get("AMORA_STAGING_EXPECTED_DATABASE", ""),
        )
    except (AivenStagingOperatorError, AivenPreflightError) as exc:
        code = str(exc)
        if not (code.startswith("PG_STAGING_") or code.startswith("AIVEN_CA_")):
            code = "PG_STAGING_OPERATOR_FAILURE"
        result = _blocked(code)
    except Exception:
        result = _blocked("PG_STAGING_UNEXPECTED_FAILURE")
    print("AIVEN_STAGING_OPERATOR " + json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] in {
        "staging_preparation_possible", "empty_staging_schema_prepared",
        "staging_structure_verified",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
