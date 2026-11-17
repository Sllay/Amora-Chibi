"""Phase 0: no live Aiven/Turso credentials, network access, or write queries."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

import pytest

from amora.storage.aiven_preflight import (
    AivenPreflightError, check_aiven_read_only, main, validate_aiven_settings,
)


_DSN = (
    "postgresql://avnadmin:secret_value@"
    "amora-database-amora-ia.f.aivencloud.com:14600/defaultdb?sslmode=require"
)
_CA = "-----BEGIN CERTIFICATE-----\nTEST-ONLY-PEM\n-----END CERTIFICATE-----\n"


class _Cursor:
    def __init__(self, *, readonly="on"):
        self.readonly = readonly
        self.queries = []
        self._latest = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql):
        self.queries.append(sql)
        self._latest = sql

    def fetchone(self):
        if self._latest == "SHOW transaction_read_only":
            return (self.readonly,)
        if self._latest == "SHOW server_version_num":
            return ("180002",)
        raise AssertionError("Unexpected SQL")


class _Connection:
    def __init__(self, readonly="on"):
        self.cursor_value = _Cursor(readonly=readonly)
        self.closed = False
        self.rolled_back = False

    def cursor(self):
        return self.cursor_value

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


@pytest.mark.parametrize("dsn", [
    "",
    "https://amora-database-amora-ia.f.aivencloud.com",
    "postgresql://avnadmin:secret_value@evil.example:14600/defaultdb",
    "postgresql://avnadmin:secret_value@amora-database-amora-ia.f.aivencloud.com/defaultdb",
    "postgresql://avnadmin@amora-database-amora-ia.f.aivencloud.com:14600/defaultdb",
    "postgresql://avnadmin:secret_value@amora-database-amora-ia.f.aivencloud.com:14600/defaultdb?sslmode=disable",
    "postgresql://avnadmin:secret_value@amora-database-amora-ia.f.aivencloud.com:14600/defaultdb?options=-c%20allow_system_table_mods=on",
])
def test_reject_untrusted_or_incomplete_aiven_connection_settings(dsn):
    with pytest.raises(AivenPreflightError, match="AIVEN_DSN_MISSING_OR_INVALID"):
        validate_aiven_settings(dsn, _CA)


def test_required_ca_certificate_not_optional():
    with pytest.raises(AivenPreflightError, match="AIVEN_CA_CERT_REQUIRED"):
        validate_aiven_settings(_DSN, "")


def test_preflight_is_tls_verified_and_issues_only_readonly_metadata_sql():
    conn = _Connection()
    captured = {}

    def connect(dsn, **options):
        captured.update(options)
        assert dsn == _DSN
        ca_path = Path(options["sslrootcert"])
        assert ca_path.read_text() == _CA
        assert stat.S_IMODE(ca_path.stat().st_mode) == 0o600
        captured["ca_path"] = ca_path
        return conn

    report = check_aiven_read_only(_DSN, _CA, connect=connect)
    assert report == {
        "status": "ok",
        "provider": "aiven-postgresql",
        "server_major": 18,
        "read_only": True,
        "tls": "verify-full",
        "core_storage_switched": False,
        "data_migration_performed": False,
    }
    assert captured["sslmode"] == "verify-full"
    assert captured["options"] == "-c default_transaction_read_only=on"
    assert not captured["ca_path"].exists()
    assert conn.cursor_value.queries == [
        "SHOW transaction_read_only",
        "SHOW server_version_num",
    ]
    assert conn.rolled_back and conn.closed
    assert "secret_value" not in json.dumps(report)


def test_driver_failing_does_not_disclose_connection_credentials():
    def unavailable(dsn, **options):
        raise RuntimeError("private: " + dsn)

    with pytest.raises(AivenPreflightError) as error:
        check_aiven_read_only(_DSN, _CA, connect=unavailable)
    assert str(error.value) == "AIVEN_CONNECTION_FAILED"
    assert "secret_value" not in str(error.value)


def test_failed_readonly_enforcement_closes_connection_without_writes():
    conn = _Connection(readonly="off")
    with pytest.raises(AivenPreflightError, match="POSTGRES_READ_ONLY_NOT_ENFORCED"):
        check_aiven_read_only(_DSN, _CA, connect=lambda *a, **k: conn)
    assert conn.closed
    assert "SHOW server_version_num" not in conn.cursor_value.queries


def test_cli_missing_secrets_fails_closed_without_changing_active_backend(
    monkeypatch, capsys
):
    monkeypatch.delenv("AMORA_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("AMORA_POSTGRES_CA_PEM", raising=False)
    monkeypatch.setenv("AMORA_STORAGE_BACKEND", "libsql")
    assert main() == 2
    output = json.loads(capsys.readouterr().out)
    assert output == {"status": "blocked", "reason": "AIVEN_DSN_MISSING_OR_INVALID"}
    assert os.environ["AMORA_STORAGE_BACKEND"] == "libsql"
