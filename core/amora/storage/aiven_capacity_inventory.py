"""Aiven PG18 live *read-only* metadata and capacity inventory.

Operator-only standalone companion to aiven_preflight.py. This never creates
schemas, reads user records or names, imports a snapshot, performs DDL/DML,
uploads secrets, or changes the Core backend. A DB-size number is NOT free
disk-space/PITR/restore certification for Aiven's 1 GB Free plan.
"""
from __future__ import annotations

import json
import os

if __package__:
    from .aiven_preflight import (
        AivenPreflightError, load_aiven_ca_certificate,
        verified_aiven_readonly_connection,
    )
else:
    # Preflight-only Northflank image copies both files under /opt/amora.
    from aiven_preflight import (  # type: ignore[no-redef]
        AivenPreflightError, load_aiven_ca_certificate,
        verified_aiven_readonly_connection,
    )


_STAGING_SCHEMA = "amora_pg_v18_lab"
_EXPECTED_MAJOR = 18


class AivenInventoryError(AivenPreflightError):
    """Only stable, non-sensitive reason codes are exposed."""


def _count(row) -> int:
    if row is None or len(row) != 1 or isinstance(row[0], bool):
        raise AivenInventoryError("AIVEN_INVENTORY_INVALID_METADATA")
    try:
        value = int(row[0])
    except (ValueError, TypeError, OverflowError):
        raise AivenInventoryError("AIVEN_INVENTORY_INVALID_METADATA") from None
    if value < 0 or value > 2**63 - 1:
        raise AivenInventoryError("AIVEN_INVENTORY_INVALID_METADATA")
    return value


def inspect_aiven_readonly(dsn: str, ca_pem: str, *, connect=None) -> dict[str, object]:
    """Read metadata ONLY after verified TLS and read-only session checks.

    The source and target object counts are estimates of *schema objects*,
    not row-level data or a guarantee that a PostgreSQL import will fit.
    """
    try:
        with verified_aiven_readonly_connection(
            dsn, ca_pem, connect=connect,
            application_name="amora-aiven-capacity-inventory",
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                if cursor.fetchone() != ("on",):
                    raise AivenInventoryError("AIVEN_INVENTORY_NOT_READ_ONLY")
                cursor.execute("SHOW server_version_num")
                major = _count(cursor.fetchone()) // 10000
                if major != _EXPECTED_MAJOR:
                    raise AivenInventoryError("AIVEN_INVENTORY_PG18_REQUIRED")

                cursor.execute("SHOW max_connections")
                connection_limit = _count(cursor.fetchone())
                cursor.execute("SELECT pg_database_size(current_database())")
                db_bytes = _count(cursor.fetchone())
                cursor.execute(
                    "SELECT COUNT(*) FROM pg_catalog.pg_namespace "
                    "WHERE nspname = %s", (_STAGING_SCHEMA,),
                )
                target_schema_count = _count(cursor.fetchone())
                cursor.execute(
                    "SELECT COUNT(*) FROM pg_catalog.pg_tables "
                    "WHERE schemaname = %s", (_STAGING_SCHEMA,),
                )
                staging_tables = _count(cursor.fetchone())
                cursor.execute(
                    "SELECT COUNT(*) FROM pg_catalog.pg_tables "
                    "WHERE schemaname NOT IN ('pg_catalog','information_schema') "
                    "AND schemaname NOT LIKE 'pg_toast%'"
                )
                user_tables = _count(cursor.fetchone())

        if target_schema_count > 1 or staging_tables > user_tables:
            raise AivenInventoryError("AIVEN_INVENTORY_INCONSISTENT_METADATA")
        return {
            "status": "inspected_read_only",
            "provider": "aiven-postgresql",
            "server_major": major,
            "tls": "verify-full",
            "read_only": True,
            "database_size_bytes": db_bytes,
            "server_max_connections": connection_limit,
            "staging_schema_exists": target_schema_count == 1,
            "staging_schema_tables": staging_tables,
            "database_user_tables": user_tables,
            "free_tier_plan_verified": False,
            "free_tier_available_disk_verified": False,
            "private_backup_freshness_verified": False,
            "data_parity_verified": False,
            "restore_tested": False,
            "aiven_import_performed": False,
            "production_cutover_authorized": False,
        }
    except (AivenPreflightError, AivenInventoryError):
        raise
    except Exception:
        raise AivenInventoryError("AIVEN_INVENTORY_QUERY_FAILED") from None


def main() -> int:
    try:
        report = inspect_aiven_readonly(
            os.environ.get("AMORA_POSTGRES_DSN", ""),
            load_aiven_ca_certificate(
                pem=os.environ.get("AMORA_POSTGRES_CA_PEM", ""),
                file_path=os.environ.get("AMORA_POSTGRES_CA_FILE", ""),
            ),
        )
    except (AivenPreflightError, AivenInventoryError) as exc:
        print(json.dumps({
            "status": "blocked",
            "reason": str(exc),
            "aiven_import_performed": False,
            "production_cutover_authorized": False,
        }, sort_keys=True))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
