"""Optional Aiven transport probe, no SQL, data import or Core activation.

Operator-only and disconnected from the default image CMD. Staged checks:
validated configured Aiven host -> DNS -> TCP -> verified TLS+Postgres connect.
No socket address, secret, DSN, CA, exception message, SQL or client IP is logged.
Only allowlisted status strings leave the process.

A TCP probe is not a PostgreSQL login; a successful TCP result does not
certify TLS, credentials, Aiven service health or readiness for production.
"""
from __future__ import annotations

import json
import os
import socket
from urllib.parse import urlsplit

if __package__:
    from .aiven_preflight import (
        AivenPreflightError, validate_aiven_settings,
        load_aiven_ca_certificate, verified_aiven_readonly_connection,
    )
else:
    from aiven_preflight import (  # type: ignore[no-redef]
        AivenPreflightError, validate_aiven_settings,
        load_aiven_ca_certificate, verified_aiven_readonly_connection,
    )


class AivenTransportError(AivenPreflightError):
    """Only stable, allowlisted reason codes; no original exception chained."""


_SQLSTATE_CODES = {
    "28P01": "AIVEN_DIAG_AUTHENTICATION_REJECTED",
    "28000": "AIVEN_DIAG_AUTHORIZATION_REJECTED",
    "3D000": "AIVEN_DIAG_DATABASE_NOT_FOUND",
    "53300": "AIVEN_DIAG_CONNECTION_LIMIT",
    "57P03": "AIVEN_DIAG_POSTGRES_NOT_READY",
}
_FALLBACK_CODE = "AIVEN_DIAG_POSTGRES_HANDSHAKE_FAILED"


def _safe_connect(connect):
    """Classify structured SQLSTATEs, never parse or echo driver error text."""
    def classified_connect(*args, **kwargs):
        try:
            return connect(*args, **kwargs)
        except Exception as exc:
            code = _SQLSTATE_CODES.get(getattr(exc, "sqlstate", None), _FALLBACK_CODE)
            raise AivenTransportError(code) from None
    return classified_connect


def _report(status, code, *, dns=False, tcp=False, postgres=False):
    return {
        "status": status,
        "reason": code,
        "dns_resolved": dns,
        "tcp_reachable": tcp,
        "postgres_connected": postgres,
        "tls_mode": "verify-full" if postgres else "not_verified",
        "sql_executed": False,
        "aiven_import_performed": False,
        "production_cutover_authorized": False,
    }


def inspect_aiven_transport(
    dsn: str, ca_pem: str, *, resolve=None, tcp_probe=None, connect=None,
) -> dict[str, object]:
    """Run only bounded operator-initiated network checks to exact Aiven host.

    No network request is made if the Aiven DSN or CA fails validation. The
    probe does not modify Aiven's IP allowlist, TLS mode or database.
    """
    try:
        validate_aiven_settings(dsn, ca_pem)
        endpoint = urlsplit(dsn)
        host, port = endpoint.hostname, endpoint.port
        if not isinstance(host, str) or not isinstance(port, int):
            raise AivenTransportError("AIVEN_DIAG_UNEXPECTED_ENDPOINT")
    except AivenPreflightError as exc:
        return _report("blocked", str(exc))
    except (ValueError, TypeError):
        return _report("blocked", "AIVEN_DIAG_UNEXPECTED_ENDPOINT")

    resolver = resolve if resolve is not None else socket.getaddrinfo
    try:
        records = resolver(host, port, type=socket.SOCK_STREAM)
        if not records:
            return _report("blocked", "AIVEN_DIAG_DNS_FAILED")
    except Exception:
        return _report("blocked", "AIVEN_DIAG_DNS_FAILED")

    probe = tcp_probe if tcp_probe is not None else socket.create_connection
    try:
        # Never request an insecure alternate destination, never print host.
        with probe((host, port), timeout=4) as connection:
            pass
    except Exception:
        return _report("blocked", "AIVEN_DIAG_TCP_FAILED", dns=True)

    if connect is None:
        try:
            import psycopg
        except ImportError:
            return _report("blocked", "POSTGRES_DRIVER_NOT_INSTALLED", dns=True, tcp=True)
        connect = psycopg.connect

    try:
        with verified_aiven_readonly_connection(
            dsn, ca_pem, connect=_safe_connect(connect),
            application_name="amora-aiven-readonly-preflight",
        ):
            # libpq completes TLS verification + PostgreSQL authentication.
            # Do not issue SQL, inspect rows, or alter transaction settings.
            pass
    except AivenPreflightError as exc:
        reason = str(exc)
        if reason not in set(_SQLSTATE_CODES.values()) | {
            _FALLBACK_CODE, "AIVEN_CONNECTION_FAILED",
            "POSTGRES_DRIVER_NOT_INSTALLED",
        }:
            reason = "AIVEN_DIAG_POSTGRES_HANDSHAKE_FAILED"
        return _report("blocked", reason, dns=True, tcp=True)
    except Exception:
        return _report("blocked", _FALLBACK_CODE, dns=True, tcp=True)

    return _report("transport_ok", "AIVEN_DIAG_CONNECTED", dns=True, tcp=True, postgres=True)


def main() -> int:
    try:
        report = inspect_aiven_transport(
            os.environ.get("AMORA_POSTGRES_DSN", ""),
            load_aiven_ca_certificate(
                pem=os.environ.get("AMORA_POSTGRES_CA_PEM", ""),
                file_path=os.environ.get("AMORA_POSTGRES_CA_FILE", ""),
            ),
        )
    except AivenPreflightError as exc:
        report = _report("blocked", str(exc))
    except Exception:
        report = _report("blocked", "AIVEN_DIAG_UNEXPECTED_FAILURE")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] == "transport_ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
