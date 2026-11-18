"""Only mocked, metadata-only Aiven operations; no credentials or external I/O."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from amora.storage.aiven_capacity_inventory import (
    AivenInventoryError, inspect_aiven_readonly, main,
)
from amora.storage.aiven_preflight import AivenPreflightError

_DSN = "postgresql://avnadmin:PRIVATE_FAKE_TEST@demo.f.aivencloud.com:14600/defaultdb?sslmode=require"
_CA = "-----BEGIN CERTIFICATE-----\nFAKE TEST ONLY\n-----END CERTIFICATE-----\n"
_QUERIES = (
    "SHOW transaction_read_only",
    "SHOW server_version_num",
    "SHOW max_connections",
    "SELECT pg_database_size(current_database())",
    "SELECT COUNT(*) FROM pg_catalog.pg_namespace WHERE nspname = %s",
    "SELECT COUNT(*) FROM pg_catalog.pg_tables WHERE schemaname = %s",
    "SELECT COUNT(*) FROM pg_catalog.pg_tables "
    "WHERE schemaname NOT IN ('pg_catalog','information_schema') "
    "AND schemaname NOT LIKE 'pg_toast%'",
)


class FakeCursor:
    def __init__(self, rows=None, fail_at=None):
        self.rows = list(rows or [
            ("on",), ("180000",), ("20",), (76493824,),
            (0,), (0,), (0,),
        ])
        self.queries = []
        self.fail_at = fail_at

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, parameters=None):
        self.queries.append((sql, parameters))
        if sql == self.fail_at:
            raise RuntimeError("forbidden private DSN: " + _DSN)

    def fetchone(self):
        return self.rows.pop(0)


class FakeConnection:
    def __init__(self, cursor=None):
        self.cur = cursor or FakeCursor()
        self.closed = False
        self.rolled_back = False

    def cursor(self):
        return self.cur

    def rollback(self):
        self.rolled_back = True

    def close(self):
        self.closed = True


def test_capacity_inventory_is_tls_verified_readonly_metadata_only():
    conn = FakeConnection()
    options = {}

    def connector(dsn, **kw):
        assert dsn == _DSN
        options.update(kw)
        assert Path(kw["sslrootcert"]).read_text() == _CA
        return conn

    data = inspect_aiven_readonly(_DSN, _CA, connect=connector)
    assert data["status"] == "inspected_read_only"
    assert data["database_size_bytes"] == 76493824
    assert data["server_max_connections"] == 20
    assert data["staging_schema_exists"] is False
    assert data["staging_schema_tables"] == 0
    assert data["database_user_tables"] == 0
    assert data["free_tier_plan_verified"] is False
    assert data["free_tier_available_disk_verified"] is False
    assert data["production_cutover_authorized"] is False
    assert data["aiven_import_performed"] is False
    assert options["sslmode"] == "verify-full"
    assert options["application_name"] == "amora-aiven-capacity-inventory"
    assert options["options"] == "-c default_transaction_read_only=on"
    assert not Path(options["sslrootcert"]).exists()
    assert conn.rolled_back and conn.closed
    assert tuple(q for q, p in conn.cur.queries) == _QUERIES
    assert all(p is None for q, p in conn.cur.queries[:4])
    assert conn.cur.queries[4][1] == ("amora_pg_v18_lab",)
    assert conn.cur.queries[5][1] == ("amora_pg_v18_lab",)
    assert "PRIVATE_FAKE_TEST" not in json.dumps(data)


def test_inventory_detects_existing_staging_schema_without_reading_data():
    conn = FakeConnection(FakeCursor(rows=[
        ("on",), ("180000",), (20,), (12_000_000,),
        (1,), (53,), (53,),
    ]))
    report = inspect_aiven_readonly(_DSN, _CA, connect=lambda *args, **kw: conn)
    assert report["staging_schema_exists"] is True
    assert report["staging_schema_tables"] == 53
    assert report["database_user_tables"] == 53
    assert report["data_parity_verified"] is False


def test_not_readonly_stops_before_any_other_query():
    conn = FakeConnection(FakeCursor(rows=[("off",)]))
    with pytest.raises(AivenInventoryError, match="AIVEN_INVENTORY_NOT_READ_ONLY"):
        inspect_aiven_readonly(_DSN, _CA, connect=lambda *args, **kw: conn)
    assert tuple(q for q, p in conn.cur.queries) == _QUERIES[:1]
    assert conn.closed and conn.rolled_back


def test_old_server_version_rejected_before_schema_inspection():
    conn = FakeConnection(FakeCursor(rows=[("on",), ("170004",)]))
    with pytest.raises(AivenInventoryError, match="AIVEN_INVENTORY_PG18_REQUIRED"):
        inspect_aiven_readonly(_DSN, _CA, connect=lambda *args, **kw: conn)
    assert tuple(q for q, p in conn.cur.queries) == _QUERIES[:2]


@pytest.mark.parametrize("rows,expected", [
    ([("on",), ("nan",)], "AIVEN_INVENTORY_INVALID_METADATA"),
    ([("on",), ("180000",), ("20",), (100,), (1,), (100,), (2,)],
     "AIVEN_INVENTORY_INCONSISTENT_METADATA"),
])
def test_invalid_metadata_blocks_without_exposing_results(rows, expected):
    conn = FakeConnection(FakeCursor(rows=rows))
    with pytest.raises(AivenInventoryError, match=expected):
        inspect_aiven_readonly(_DSN, _CA, connect=lambda *args, **kw: conn)
    assert conn.closed


def test_db_errors_redacted():
    conn = FakeConnection(FakeCursor(fail_at="SHOW max_connections"))
    with pytest.raises(AivenPreflightError) as e:
        inspect_aiven_readonly(_DSN, _CA, connect=lambda *args, **kw: conn)
    assert "PRIVATE_FAKE_TEST" not in str(e.value)
    assert str(e.value) == "AIVEN_CONNECTION_FAILED"
    assert conn.closed


def test_wrong_url_rejected_before_opening_socket():
    def forbidden(*args, **kwargs):
        raise AssertionError("network access forbidden")
    with pytest.raises(AivenPreflightError, match="AIVEN_DSN_MISSING_OR_INVALID"):
        inspect_aiven_readonly(
            _DSN.replace(".aivencloud.com", ".example.invalid"), _CA,
            connect=forbidden,
        )


def test_cli_no_credentials_emits_only_stable_block_code(monkeypatch, capsys):
    monkeypatch.delenv("AMORA_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("AMORA_POSTGRES_CA_FILE", raising=False)
    monkeypatch.delenv("AMORA_POSTGRES_CA_PEM", raising=False)
    assert main() == 2
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "blocked"
    assert output["reason"] == "AIVEN_DSN_MISSING_OR_INVALID"
    assert output["aiven_import_performed"] is False
