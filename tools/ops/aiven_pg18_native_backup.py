"""One-shot native PG18 staging backup to the existing /private recovery volume.

Reads a pinned Aiven PostgreSQL 18 target with TLS verify-full. Does not open
private memory content in Python and never logs credentials or row payloads.
This is NOT an offsite or independently restored backup until the sealed
artifact is exported and restored in an isolated PostgreSQL 18 instance.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from urllib.parse import unquote, urlsplit


ARCHIVE = Path("/private/pcs4c/backup/amora-aiven-pg18-v18-prewrite.dump")
CONFIRM = "CREATE_ONE_NATIVE_PG18_PREWRITE_BACKUP"
SCHEMA = "amora_pg_v18_lab"


class BackupBlocked(Exception):
    pass


def _environment() -> dict[str, str]:
    dsn = os.getenv("AMORA_POSTGRES_DSN", "")
    ca_path = os.getenv("AMORA_POSTGRES_CA_FILE", "")
    try:
        u = urlsplit(dsn)
        user = unquote(u.username or "")
        password = unquote(u.password or "")
        host = u.hostname or ""
        port = u.port or 0
        database = unquote(u.path.removeprefix("/"))
        expected_host = os.getenv("AMORA_STAGING_EXPECTED_HOST", "")
        expected_port = int(os.getenv("AMORA_STAGING_EXPECTED_PORT", "0"))
        expected_database = os.getenv("AMORA_STAGING_EXPECTED_DATABASE", "")
        if (u.scheme not in {"postgresql", "postgres"} or not user or not password
                or not host or not port or not database
                or not expected_host or not expected_port or not expected_database
                or host != expected_host or port != expected_port
                or database != expected_database
                or not host.endswith(".aivencloud.com")
                or not Path(ca_path).is_absolute()
                or not Path(ca_path).is_file()
                or Path(ca_path).stat().st_size < 500
                or os.getenv("AMORA_POSTGRES_CA_PEM")):
            raise BackupBlocked("PG_BACKUP_TARGET_OR_CA_INVALID")
    except (ValueError, OSError):
        raise BackupBlocked("PG_BACKUP_CONFIGURATION_INVALID") from None

    env = dict(os.environ)
    # libpq reads all sensitive fields from the child environment, not argv
    # or stdout. Query-string DSN options are deliberately not forwarded.
    env.update(
        PGHOST=host,
        PGPORT=str(port),
        PGDATABASE=database,
        PGUSER=user,
        PGPASSWORD=password,
        PGSSLMODE="verify-full",
        PGSSLROOTCERT=ca_path,
        PGCONNECT_TIMEOUT="8",
        PGOPTIONS="-c default_transaction_read_only=on",
    )
    return env


def _call(args: list[str], env: dict[str, str], *, timeout: int) -> str:
    try:
        result = subprocess.run(
            args, check=True, env=env, timeout=timeout,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        )
        if len(result.stdout) > 1024 * 1024:
            raise BackupBlocked("PG_BACKUP_UNEXPECTED_OUTPUT")
        return result.stdout.decode("utf-8", "strict").strip()
    except (subprocess.SubprocessError, OSError, UnicodeError):
        raise BackupBlocked("PG_BACKUP_CLIENT_COMMAND_FAILED") from None


def create_backup() -> dict[str, object]:
    if not ARCHIVE.parent.is_dir() or not str(ARCHIVE).startswith("/private/pcs4c/backup/"):
        raise BackupBlocked("PG_BACKUP_DURABLE_MOUNT_UNAVAILABLE")
    if ARCHIVE.exists() or ARCHIVE.with_suffix(".dump.tmp").exists():
        raise BackupBlocked("PG_BACKUP_ALREADY_EXISTS_REVIEW_FIRST")
    if shutil.disk_usage(ARCHIVE.parent).free < 192 * 1024 * 1024:
        raise BackupBlocked("PG_BACKUP_VOLUME_CAPACITY_INSUFFICIENT")
    env = _environment()
    if not shutil.which("pg_dump") or not shutil.which("pg_restore") or not shutil.which("psql"):
        raise BackupBlocked("PG_BACKUP_PG18_CLIENT_UNAVAILABLE")

    server = _call(
        ["psql", "-X", "-A", "-t", "-c",
         "SELECT current_setting('server_version_num')"],
        env, timeout=30,
    )
    if not server.isdecimal() or int(server) // 10000 != 18:
        raise BackupBlocked("PG_BACKUP_SERVER_MAJOR_MISMATCH")
    versions = _call(
        ["psql", "-X", "-A", "-t", "-c",
         "SELECT COUNT(*),MIN(version),MAX(version) "
         "FROM amora_pg_v18_lab.schema_migrations"],
        env, timeout=30,
    )
    if versions != "18|1|18":
        raise BackupBlocked("PG_BACKUP_CANONICAL_SCHEMA_NOT_READY")

    temp = ARCHIVE.with_suffix(".dump.tmp")
    old_umask = os.umask(0o077)
    try:
        # Exclusive creation and no truncation of an existing artifact.
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb"):
            pass
        _call(
            ["pg_dump", "--format=custom", "--no-owner", "--no-acl",
             "--schema=" + SCHEMA, "--file=" + str(temp)],
            env, timeout=600,
        )
        size = temp.stat().st_size
        if size < 4096:
            raise BackupBlocked("PG_BACKUP_DUMP_TOO_SMALL")
        listing = _call(
            ["pg_restore", "--list", str(temp)],
            env, timeout=60,
        )
        if SCHEMA not in listing or "memory_records" not in listing:
            raise BackupBlocked("PG_BACKUP_ARCHIVE_STRUCTURE_INVALID")
        # Release to the durable volume only after successful format checks.
        # Link fails if a final archive already exists; it never overwrites.
        os.link(temp, ARCHIVE)
        temp.unlink()
        return {
            "status": "native_prewrite_dump_on_private_volume",
            "format": "pg_dump_custom_v18",
            "size_bytes": size,
            "schema_version": 18,
            "stored_in_durable_volume": True,
            "independent_restore_verified": False,
            "offsite_backup_verified": False,
            "core_storage_switched": False,
            "production_cutover_authorized": False,
        }
    finally:
        os.umask(old_umask)
        temp.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Aiven PG18 one-shot backup")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args()
    try:
        if args.confirm != CONFIRM:
            raise BackupBlocked("PG_BACKUP_OPERATOR_CONFIRMATION_REQUIRED")
        result = create_backup()
    except BackupBlocked as exc:
        result = {"status": "blocked", "reason": str(exc)}
    except Exception:
        result = {"status": "blocked", "reason": "PG_BACKUP_UNKNOWN_FAILURE"}
    result.setdefault("independent_restore_verified", False)
    result.setdefault("offsite_backup_verified", False)
    result.setdefault("core_storage_switched", False)
    result.setdefault("production_cutover_authorized", False)
    print("AIVEN_NATIVE_BACKUP " + json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] == "native_prewrite_dump_on_private_volume" else 2


if __name__ == "__main__":
    sys.exit(main())
