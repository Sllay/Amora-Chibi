"""Aiven PostgreSQL connectivity preflight, strictly isolated from serving Core.

This module does NOT implement a storage backend, create tables, migrate
snapshots, change AMORA_STORAGE_BACKEND, or access Turso. The DSN stays
in a private environment variable and CA certificate may be provided as a
Northflank Secret File. Neither value is written to logs.
"""
from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit


class AivenPreflightError(RuntimeError):
    """Structured, non-secret error. Never include raw driver exceptions."""


_MAX_CA_FILE_BYTES = 65536


def load_aiven_ca_certificate(*, pem: str = "", file_path: str = "") -> str:
    """Load a mounted secret file without exposing paths or content in errors.

    The file and inline PEM settings are mutually exclusive; both missing is
    rejected by the existing TLS configuration validator.
    """
    if file_path and pem:
        raise AivenPreflightError("AIVEN_CA_SOURCE_AMBIGUOUS")
    if not file_path:
        return pem
    if (
        len(file_path) > 2048
        or not Path(file_path).is_absolute()
        or "\x00" in file_path
    ):
        raise AivenPreflightError("AIVEN_CA_FILE_UNAVAILABLE")
    try:
        file = Path(file_path)
        size = file.stat().st_size
        if not 0 < size <= _MAX_CA_FILE_BYTES or not file.is_file():
            raise AivenPreflightError("AIVEN_CA_FILE_UNAVAILABLE")
        with file.open("rb") as stream:
            raw = stream.read(_MAX_CA_FILE_BYTES + 1)
        if len(raw) > _MAX_CA_FILE_BYTES:
            raise AivenPreflightError("AIVEN_CA_FILE_UNAVAILABLE")
        return raw.decode("utf-8")
    except AivenPreflightError:
        raise
    except (OSError, UnicodeError, ValueError):
        # Never expose mount paths, certificate text or driver details.
        raise AivenPreflightError("AIVEN_CA_FILE_UNAVAILABLE") from None


def validate_aiven_settings(dsn: str, ca_pem: str) -> None:
    """Fail closed before opening a socket or importing a PostgreSQL driver."""
    if not dsn or len(dsn) > 4096:
        raise AivenPreflightError("AIVEN_DSN_MISSING_OR_INVALID")
    try:
        parsed = urlsplit(dsn)
        params = parse_qsl(parsed.query, strict_parsing=True, keep_blank_values=True)
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


@contextmanager
def verified_aiven_readonly_connection(
    dsn: str, ca_pem: str, *, connect=None,
    application_name: str = "amora-aiven-readonly-preflight",
):
    """Shared no-echo TLS-verified, read-only Aiven connection.

    The context never returns credentials/CA to logs, never switches Core
    storage, and always rolls back then closes the connection.
    """
    validate_aiven_settings(dsn, ca_pem)
    if connect is None:
        try:
            import psycopg
        except ImportError:
            raise AivenPreflightError("POSTGRES_DRIVER_NOT_INSTALLED") from None
        connect = psycopg.connect
    if application_name not in {
        "amora-aiven-readonly-preflight",
        "amora-aiven-capacity-inventory",
    }:
        raise AivenPreflightError("AIVEN_UNREVIEWED_APPLICATION_NAME")
    with tempfile.TemporaryDirectory(prefix="amora-aiven-check-") as folder:
        ca_path = Path(folder) / "ca.pem"
        fd = os.open(str(ca_path), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as destination:
            destination.write(ca_pem)
        connection = None
        try:
            try:
                connection = connect(
                    dsn,
                    sslmode="verify-full",
                    sslrootcert=str(ca_path),
                    connect_timeout=8,
                    application_name=application_name,
                    options="-c default_transaction_read_only=on",
                )
            except AivenPreflightError:
                # The explicitly audited optional transport probe can return
                # a fixed SQLSTATE-derived reason without exposing psycopg text.
                raise
            except Exception:
                # Only socket/TLS/authentication failures are connection errors.
                # Driver exception text may include secrets; never log/chain it.
                raise AivenPreflightError("AIVEN_CONNECTION_FAILED") from None

            try:
                yield connection
            except AivenPreflightError:
                raise
            except Exception:
                # The connection was established. A query/cursor/metadata
                # failure must NOT masquerade as a network/DSN problem.
                raise AivenPreflightError("AIVEN_READONLY_OPERATION_FAILED") from None
        finally:
            if connection is not None:
                try:
                    connection.rollback()
                except Exception:
                    pass
                try:
                    connection.close()
                except Exception:
                    pass


def check_aiven_read_only(
    dsn: str,
    ca_pem: str,
    *,
    connect=None,
) -> dict[str, object]:
    """Connect with TLS hostname verification and read-only session semantics.

    Only two metadata queries, no application data or schema inspection.
    """
    with verified_aiven_readonly_connection(
        dsn, ca_pem, connect=connect,
    ) as connection:
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
        return {
            "status": "ok",
            "provider": "aiven-postgresql",
            "server_major": major,
            "read_only": True,
            "tls": "verify-full",
            "core_storage_switched": False,
            "data_migration_performed": False,
        }


def main() -> int:
    try:
        result = check_aiven_read_only(
            os.environ.get("AMORA_POSTGRES_DSN", ""),
            load_aiven_ca_certificate(
                pem=os.environ.get("AMORA_POSTGRES_CA_PEM", ""),
                file_path=os.environ.get("AMORA_POSTGRES_CA_FILE", ""),
            ),
        )
    except AivenPreflightError as exc:
        print(json.dumps({"status": "blocked", "reason": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
