"""Aiven transport diagnostic contracts: zero real network or database access."""
from __future__ import annotations

from pathlib import Path
import json

import pytest

from amora.storage.aiven_transport_diagnostic import (
    inspect_aiven_transport, main,
)

_DSN = (
    "postgresql://avnadmin:NEVER_PRINT_TEST_SECRET@"
    "synthetic-project.f.aivencloud.com:14600/defaultdb?sslmode=require"
)
_CA = "-----BEGIN CERTIFICATE-----\nFAKE_ONLY\n-----END CERTIFICATE-----"


class Socket:
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False


class Raw:
    def __init__(self):
        self.closed = False
        self.rolled_back = False
        self.queries = []
    def rollback(self):
        self.rolled_back = True
    def close(self):
        self.closed = True
    def cursor(self):
        raise AssertionError("No SQL or cursor is allowed")


def _dns(host, port, *, type):
    assert host == "synthetic-project.f.aivencloud.com"
    assert port == 14600
    return [object()]


def _tcp(destination, timeout):
    assert destination == ("synthetic-project.f.aivencloud.com", 14600)
    assert timeout == 4
    return Socket()


def test_success_only_makes_tls_verified_connection_and_runs_no_sql():
    raw = Raw()
    ca_path = []

    def connect(dsn, **kwargs):
        assert dsn == _DSN
        assert kwargs["sslmode"] == "verify-full"
        assert kwargs["options"] == "-c default_transaction_read_only=on"
        ca = Path(kwargs["sslrootcert"])
        assert ca.read_text() == _CA
        ca_path.append(ca)
        return raw

    report = inspect_aiven_transport(
        _DSN, _CA, resolve=_dns, tcp_probe=_tcp, connect=connect,
    )
    assert report == {
        "status": "transport_ok",
        "reason": "AIVEN_DIAG_CONNECTED",
        "dns_resolved": True,
        "tcp_reachable": True,
        "postgres_connected": True,
        "tls_mode": "verify-full",
        "sql_executed": False,
        "aiven_import_performed": False,
        "production_cutover_authorized": False,
    }
    assert raw.rolled_back and raw.closed
    assert not ca_path[0].exists()
    assert "NEVER_PRINT_TEST_SECRET" not in json.dumps(report)


def test_invalid_settings_never_use_dns_tcp_or_database():
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden")
    for dsn, ca in [
        ("", _CA),
        (_DSN.replace(".aivencloud.com", ".example.invalid"), _CA),
        (_DSN, ""),
    ]:
        report = inspect_aiven_transport(
            dsn, ca, resolve=forbidden, tcp_probe=forbidden, connect=forbidden,
        )
        assert report["status"] == "blocked"
        assert report["dns_resolved"] is False
        assert report["tcp_reachable"] is False
        assert report["sql_executed"] is False


def test_failed_dns_blocks_before_tcp_or_postgres():
    def dns_failure(*args, **kwargs):
        raise RuntimeError("NEVER_PRINT_TEST_SECRET DNS failure")
    def forbidden(*args, **kwargs):
        raise AssertionError("Must not connect")

    report = inspect_aiven_transport(
        _DSN, _CA, resolve=dns_failure, tcp_probe=forbidden, connect=forbidden,
    )
    assert report["reason"] == "AIVEN_DIAG_DNS_FAILED"
    assert report["dns_resolved"] is False
    assert report["tcp_reachable"] is False
    assert "NEVER_PRINT_TEST_SECRET" not in json.dumps(report)


def test_failed_tcp_blocks_before_database_auth():
    def tcp_failure(*args, **kwargs):
        raise RuntimeError("NEVER_PRINT_TEST_SECRET TCP failure")
    def forbidden(*args, **kwargs):
        raise AssertionError("Must not connect")

    report = inspect_aiven_transport(
        _DSN, _CA, resolve=_dns, tcp_probe=tcp_failure, connect=forbidden,
    )
    assert report["reason"] == "AIVEN_DIAG_TCP_FAILED"
    assert report["dns_resolved"] is True
    assert report["tcp_reachable"] is False


@pytest.mark.parametrize("sqlstate,reason", [
    ("28P01", "AIVEN_DIAG_AUTHENTICATION_REJECTED"),
    ("28000", "AIVEN_DIAG_AUTHORIZATION_REJECTED"),
    ("3D000", "AIVEN_DIAG_DATABASE_NOT_FOUND"),
    ("53300", "AIVEN_DIAG_CONNECTION_LIMIT"),
    ("57P03", "AIVEN_DIAG_POSTGRES_NOT_READY"),
    (None, "AIVEN_DIAG_POSTGRES_HANDSHAKE_FAILED"),
    ("08001", "AIVEN_DIAG_POSTGRES_HANDSHAKE_FAILED"),
])
def test_connect_failure_codes_redacted(sqlstate, reason):
    class DriverFault(RuntimeError):
        def __init__(self):
            super().__init__(_DSN)
            self.sqlstate = sqlstate
    def connect(*args, **kwargs):
        raise DriverFault()

    report = inspect_aiven_transport(
        _DSN, _CA, resolve=_dns, tcp_probe=_tcp, connect=connect,
    )
    assert report["status"] == "blocked"
    assert report["reason"] == reason
    assert report["dns_resolved"] is True
    assert report["tcp_reachable"] is True
    assert report["postgres_connected"] is False
    assert report["tls_mode"] == "not_verified"
    assert "NEVER_PRINT_TEST_SECRET" not in json.dumps(report)


def test_main_missing_secrets_fails_closed_without_network(monkeypatch, capsys):
    monkeypatch.delenv("AMORA_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("AMORA_POSTGRES_CA_FILE", raising=False)
    monkeypatch.delenv("AMORA_POSTGRES_CA_PEM", raising=False)
    assert main() == 2
    report = json.loads(capsys.readouterr().out)
    assert report["reason"] == "AIVEN_DSN_MISSING_OR_INVALID"
    assert report["aiven_import_performed"] is False
    assert report["sql_executed"] is False
    assert report["production_cutover_authorized"] is False
