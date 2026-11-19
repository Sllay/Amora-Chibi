"""Manual PCS4C -> Aiven isolated PG18 bridge; no source-volume writes.

Intended ONLY for the existing pcs4c-recovery manual job with its original
/private volume mounted. No backup bytes, private messages, hashes or paths
are emitted. Default 'inspect' never opens a network connection. 'import'
requires explicit attestations, a pinned Aiven TLS destination, and the
already-prepared EMPTY canonical staging schema.

The operator never touches the original Turso source, drops tables, starts
Core/Discord, or authorizes production cutover. Unknown PostgreSQL commit
outcomes require manual destination inspection; NEVER blindly retry.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import os
from pathlib import Path
import re
import tempfile
from urllib.parse import urlsplit

from .aiven_preflight import (
    AivenPreflightError, load_aiven_ca_certificate, validate_aiven_settings,
)
from .aiven_snapshot_audit import (
    DEFAULT_SNAPSHOT, DEFAULT_STATE, SnapshotAuditError, audit,
)
from .pcs4_web import _mounted
from .postgres_private_staging_import import (
    PrivateStagingImportError, import_private_staging_v18,
)
from .postgres_run3_snapshot_preflight import (
    Run3SnapshotError, inspect_private_snapshot,
)
from .postgres_runtime_connection import PostgresTargetPolicy
from .postgres_v18_lab_schema import LAB_SCHEMA


class PrivateBridgeError(RuntimeError):
    """Fixed error code only; never include private material or raw exceptions."""


_IMPORT_CONFIRM = "IMPORT_VERIFIED_V18_TO_EMPTY_ISOLATED_STAGING"
_FIXED_VOLUME = Path("/private")
_SAFE_CODE = re.compile(r"(?:PG_PRIVATE|AIVEN_CA)_[A-Z0-9_]{1,85}\Z")


def _blocked(code: str, *, target_requires_review: bool = False) -> dict[str, object]:
    return {
        "status": "blocked", "reason": code,
        "target_requires_review": target_requires_review,
        "private_import_performed": False if not target_requires_review else None,
        "core_storage_switched": False,
        "production_cutover_authorized": False,
    }


def _audit_source(*, mount_ok=None, audit_source=None, inspect_source=None):
    """Check durable mount + PCS4C pin + exact DDL before opening network."""
    check_mount = mount_ok or _mounted
    if not check_mount(_FIXED_VOLUME):
        raise PrivateBridgeError("PG_PRIVATE_DURABLE_MOUNT_REQUIRED")
    check_audit = audit_source or audit
    check_schema = inspect_source or inspect_private_snapshot
    try:
        existing = check_audit(DEFAULT_SNAPSHOT, DEFAULT_STATE)
        strict = check_schema(DEFAULT_SNAPSHOT)
        if (existing["status"] != "ok" or strict["status"] != "verified_offline"
                or existing["schema_version"] != 18
                or strict["schema_version"] != 18
                or existing["tables"] != strict["logical_tables"]
                or existing["rows"] != strict["logical_rows"]
                or existing["assets"] != strict["avatar_assets"]):
            raise PrivateBridgeError("PG_PRIVATE_SOURCE_CERTIFICATION_DRIFT")
    except PrivateBridgeError:
        raise
    except (SnapshotAuditError, Run3SnapshotError):
        raise PrivateBridgeError("PG_PRIVATE_SOURCE_CERTIFICATION_BLOCKED") from None
    except Exception:
        raise PrivateBridgeError("PG_PRIVATE_SOURCE_VERIFICATION_FAILED") from None
    return strict


def _target_policy(dsn: str, ca_pem: str, *, host: str, port: int, database: str):
    try:
        validate_aiven_settings(dsn, ca_pem)
        endpoint = urlsplit(dsn)
        actual_host = endpoint.hostname
        actual_port = endpoint.port
        actual_database = endpoint.path[1:]
        if not host or not port or not database:
            raise PrivateBridgeError("PG_PRIVATE_ENDPOINT_PIN_REQUIRED")
        if (not actual_host or not actual_port or not actual_database
                or "/" in actual_database or "%" in actual_database
                or "\x00" in actual_database):
            raise PrivateBridgeError("PG_PRIVATE_ENDPOINT_INVALID")
        if (actual_host != host or actual_port != port
                or actual_database != database):
            raise PrivateBridgeError("PG_PRIVATE_ENDPOINT_PIN_MISMATCH")
        return PostgresTargetPolicy(
            host=actual_host, port=actual_port, database=actual_database,
            schema=LAB_SCHEMA, server_major=18, require_tls=True,
        )
    except PrivateBridgeError:
        raise
    except Exception:
        raise PrivateBridgeError("PG_PRIVATE_CONNECTION_SETTINGS_INVALID") from None


def inspect_or_import(
    *, mode: str, dsn: str = "", ca_pem: str = "",
    expected_host: str = "", expected_port: int = 0,
    expected_database: str = "", confirmation: str = "",
    source_freshness_attested: bool = False,
    external_backup_restore_attested: bool = False,
    isolated_target_attested: bool = False,
    free_plan_capacity_attested: bool = False,
    connect=None, import_target=None, mount_ok=None,
    audit_source=None, inspect_source=None,
) -> dict[str, object]:
    """Inspectable default, non-retrying write path with independent gates."""
    if mode not in {"inspect", "import"}:
        raise PrivateBridgeError("PG_PRIVATE_MODE_INVALID")
    if mode == "import" and (
        confirmation != _IMPORT_CONFIRM
        or not source_freshness_attested
        or not external_backup_restore_attested
        or not isolated_target_attested
        or not free_plan_capacity_attested
    ):
        # Even mounted snapshot must not be accessed before a write approval.
        raise PrivateBridgeError("PG_PRIVATE_OPERATOR_GATES_REQUIRED")

    source = _audit_source(
        mount_ok=mount_ok, audit_source=audit_source,
        inspect_source=inspect_source,
    )
    if mode == "inspect":
        return {
            "status": "private_source_ready_for_staging",
            "operation": "offline_source_inspect",
            "logical_tables": source["logical_tables"],
            "logical_rows": source["logical_rows"],
            "avatar_assets": source["avatar_assets"],
            "schema_version": 18,
            "private_snapshot_bytes": source["snapshot_bytes"],
            "schema": "canonical_v18_exact",
            "network_access": False,
            "private_import_performed": False,
            "source_freshness_independently_verified": False,
            "external_backup_restore_independently_verified": False,
            "core_storage_switched": False,
            "production_cutover_authorized": False,
        }

    policy = _target_policy(
        dsn, ca_pem, host=expected_host, port=expected_port,
        database=expected_database,
    )
    if connect is None:
        try:
            import psycopg
            connect = psycopg.connect
        except ImportError:
            raise PrivateBridgeError("PG_PRIVATE_DRIVER_NOT_INSTALLED") from None

    with tempfile.TemporaryDirectory(prefix="amora-private-transfer-ca-") as folder:
        ca_path = Path(folder) / "ca.pem"
        fd = os.open(str(ca_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(ca_pem)
        try:
            raw = connect(
                dsn,
                sslmode="verify-full", sslrootcert=str(ca_path),
                application_name="amora-private-pcs4c-staging-import",
                connect_timeout=8,
                options="-c default_transaction_read_only=off",
                autocommit=True,
            )
        except Exception:
            raise PrivateBridgeError("PG_PRIVATE_CONNECTION_FAILED") from None

        with closing(raw):
            try:
                policy.verify(raw)
            except Exception:
                raise PrivateBridgeError("PG_PRIVATE_DESTINATION_NOT_VERIFIED") from None
            importer = import_target or import_private_staging_v18
            try:
                migrated = importer(
                    DEFAULT_SNAPSHOT, raw, policy, confirmation=_IMPORT_CONFIRM,
                )
                if (migrated.get("status") != "staged_import_completed"
                        or migrated.get("typed_row_parity") is not True
                        or migrated.get("production_cutover_authorized") is not False):
                    raise PrivateBridgeError("PG_PRIVATE_IMPORT_REPORT_INVALID")
            except PrivateBridgeError:
                raise
            except PrivateStagingImportError as exc:
                code = str(exc)
                if not _SAFE_CODE.fullmatch(code):
                    code = "PG_PRIVATE_IMPORT_REVIEW_REQUIRED"
                raise PrivateBridgeError(code) from None
            except Exception:
                raise PrivateBridgeError("PG_PRIVATE_IMPORT_REVIEW_REQUIRED") from None
            return {
                **migrated,
                "operation": "one_shot_private_staging_import",
                "operator_attested_source_freshness": source_freshness_attested,
                "operator_attested_external_backup_restore":
                    external_backup_restore_attested,
                "operator_attested_target_isolation": isolated_target_attested,
                "operator_attested_capacity": free_plan_capacity_attested,
                "external_backup_restore_independently_verified": False,
                "core_storage_switched": False,
                "production_cutover_authorized": False,
            }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("inspect", "import"))
    parser.add_argument("--confirm", default="")
    parser.add_argument("--source-freshness-attested", action="store_true")
    parser.add_argument("--external-backup-restore-attested", action="store_true")
    parser.add_argument("--isolated-target-attested", action="store_true")
    parser.add_argument("--free-plan-capacity-attested", action="store_true")
    args = parser.parse_args(argv)

    try:
        ca_pem = ""
        if args.mode == "import":
            ca_pem = load_aiven_ca_certificate(
                pem=os.environ.get("AMORA_POSTGRES_CA_PEM", ""),
                file_path=os.environ.get("AMORA_POSTGRES_CA_FILE", ""),
            )
        result = inspect_or_import(
            mode=args.mode,
            dsn=os.environ.get("AMORA_POSTGRES_DSN", ""),
            ca_pem=ca_pem,
            expected_host=os.environ.get("AMORA_STAGING_EXPECTED_HOST", ""),
            expected_port=int(os.environ.get("AMORA_STAGING_EXPECTED_PORT", "0")),
            expected_database=os.environ.get("AMORA_STAGING_EXPECTED_DATABASE", ""),
            confirmation=args.confirm,
            source_freshness_attested=args.source_freshness_attested,
            external_backup_restore_attested=args.external_backup_restore_attested,
            isolated_target_attested=args.isolated_target_attested,
            free_plan_capacity_attested=args.free_plan_capacity_attested,
        )
    except PrivateBridgeError as exc:
        result = _blocked(str(exc))
    except AivenPreflightError as exc:
        code = str(exc)
        result = _blocked(code if _SAFE_CODE.fullmatch(code)
                          else "PG_PRIVATE_CA_UNAVAILABLE")
    except Exception:
        result = _blocked("PG_PRIVATE_UNEXPECTED_FAILURE")

    print("AIVEN_PRIVATE_BRIDGE " + json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] in {
        "private_source_ready_for_staging", "staged_import_completed",
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
