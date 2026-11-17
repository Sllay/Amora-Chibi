"""Aiven PostgreSQL connectivity preflight, strictly isolated from serving Core.

This module does NOT implement a storage backend, create tables, migrate
snapshots, change AMORA_STORAGE_BACKEND, or access Turso. All connection
secrets remain in environment variables and are never written to logs.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


class AivenPreflightError(RuntimeError):
    """Structured, non-secret error. Never include raw driver exceptions."""


def validate_aiven_settings(dsn: str, ca_pem: str) -> None:
    """Fail closed before opening a socket or importing a PostgreSQL driver."""
    if not dsn or len(dsn) > 4096:
        raise AivenPreflightError("AIVEN_DSN_MISSING_OR_INVALID")
    try:
        parsed = urlsplit(dsn)
        params = parse_qsl(parsed.query, strict_parsing=True, keep_blank_values=False)
        valid = (
            parsed.scheme in {"postgres", "postgresql"}
            and parsed.hostname is not None
            and parsed.hostname.endswith(".aivencloud.com")
            and parsed.hostname.count(".") >= 2
            and parsed.port is not None
            and parsed.username is not None
            and parsed.password is not None
            and parsed.path not in {"", "/"}
            and not parsed.fragment
            and len(params) <= 1
            and all(k == "sslmode" and v in {"require", "verify-ca", "verify-full"}
                    for k, v in params)
        )
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        raise AivenPreflightError("AIVEN_DSN_MISSING_OR_INVALID")
    if (
        not ca_pem
        or len(ca_pem) > 65536
        or "-----BEGIN CERTIFICATE-----" not in ca_pem
        or "-----END CERTIFICATE-----" not in ca_pem
    ):
        raise AivenPreflightError("AIVEN_CA_CERT_REQUIRED")


def check_aiven_read_only(
    dsn: str,
    ca_pem: str,
    *,
    connect=None,
) -> dict[str, object]:
    """Connect with TLS hostname verification and read-only session semantics.

    The CA is held only in a private, temporary mode-0600 file. The two SQL
    statements are metadata-only and never read application data.
    """
    validate_aiven_settings(dsn, ca_pem)
    if connect is None:
        try:
            import psycopg
        except ImportError as exc:
            raise AivenPreflightError("POSTGRES_DRIVER_NOT_INSTALLED") from exc
        connect = psycopg.connect

    with tempfile.TemporaryDirectory(prefix="amora-aiven-check-") as folder:
        ca_path = Path(folder) / "ca.pem"
        fd = os.open(str(ca_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            target.write(ca_pem)
        connection = None
        try:
            # Override the Aiven-provided default sslmode=require, which does
            # NOT verify the server certificate or hostname.
            connection = connect(
                dsn,
                sslmode="verify-full",
                sslrootcert=str(ca_path),
                connect_timeout=8,
                application_name="amora-aiven-readonly-preflight",
                options="-c default_transaction_read_only=on",
            )
            with connection.cursor() as cursor:
                cursor.execute("SHOW transaction_read_only")
                readonly = cursor.fetchone()
                if not readonly or readonly[0] != "on":
                    raise AivenPreflightError("POSTGRES_READ_ONLY_NOT_ENFORCED")
                cursor.execute("SHOW server_version_num")
                version = cursor.fetchone()
                if not version or not str(version[0]).isdigit():
                    raise AivenPreflightError("POSTGRES_VERSION_UNAVAILABLE")
                major = int(str(version[0])) // 10000
            connection.rollback()
            return {
                "status": "ok",
                "provider": "aiven-postgresql",
                "server_major": major,
                "read_only": True,
                "tls": "verify-full",
                "core_storage_switched": False,
                "data_migration_performed": False,
            }
        except AivenPreflightError:
            raise
        except Exception as exc:
            # PostgreSQL errors may repeat DSNs, usernames, hostnames, or SQL
            # parameters. Do not surface underlying exception text.
            raise AivenPreflightError("AIVEN_CONNECTION_FAILED") from exc
        finally:
            if connection is not None:
                try:
                    connection.close()
                except Exception:
                    pass


def main() -> int:
    try:
        result = check_aiven_read_only(
            os.environ.get("AMORA_POSTGRES_DSN", ""),
            os.environ.get("AMORA_POSTGRES_CA_PEM", ""),
        )
    except AivenPreflightError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
