"""Staging operator contract: no live Aiven connection in CI."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amora.storage import aiven_staging_operator as operator

_DSN = (
    "postgresql://avnadmin:NEVER_PRINT_TEST_SECRET@"
    "synthetic-project.f.aivencloud.com:14600/defaultdb?sslmode=require"
)
_CA = "-----BEGIN CERTIFICATE-----\nDUMMY_ONLY\n-----END CERTIFICATE-----"
_PIN = {
    "expected_host": "synthetic-project.f.aivencloud.com",
    "expected_port": 14600,
    "expected_database": "defaultdb",
}


class FakeRaw:
    autocommit = True
    closed = False

    def __init__(self, *, schemas=0, tables=0, bytes_used=8100000):
        self.info = SimpleNamespace(
            host="synthetic-project.f.aivencloud.com",
            port=14600, dbname="defaultdb", server_version=180003,
            get_parameters=lambda: {"sslmode": "verify-full"},
        )
        self.pgconn = SimpleNamespace(ssl_in_use=True)
        self.schemas, self.tables, self.bytes_used = schemas, tables, bytes_used
        self.queries = []

    def execute(self, sql, params=None):
        self.queries.append((sql, params))
        if "pg_namespace" in sql:
            value = self.schemas
        elif "pg_tables" in sql:
            value = self.tables
        elif "pg_database_size" in sql:
            value = self.bytes_used
        else:
            raise AssertionError("Unreviewed SQL")
        return SimpleNamespace(fetchone=lambda: (value,))

    def close(self):
        self.closed = True


def _fake_connector(raw, seen):
    def connect(dsn, **kwargs):
        assert dsn == _DSN
        assert kwargs["sslmode"] == "verify-full"
        assert kwargs["autocommit"] is True
        assert kwargs["connect_timeout"] == 8
        ca = Path(kwargs["sslrootcert"])
        assert ca.read_text() == _CA
        assert ca.stat().st_mode & 0o077 == 0
        seen.append((kwargs, ca))
        return raw
    return connect


def _prepare_kwargs():
    return {
        "mode": "prepare", "dsn": _DSN, "ca_pem": _CA,
        "confirmation": "PREPARE_EMPTY_PG18_ISOLATED_STAGING_ONLY",
        "snapshot_attested": True,
        "external_backup_restore_attested": True,
        "isolated_target_attested": True,
        "free_plan_capacity_attested": True,
        **_PIN,
    }


def test_inspect_connects_readonly_and_executes_no_ddl():
    raw = FakeRaw()
    seen = []
    report = operator.inspect_or_prepare(
        mode="inspect", dsn=_DSN, ca_pem=_CA,
        **_PIN,
        connect=_fake_connector(raw, seen),
    )
    assert report["status"] == "staging_preparation_possible"
    assert report["operation"] == "read_only_inspect"
    assert report["database_user_tables"] == 0
    assert report["aiven_import_performed"] is False
    assert report["production_cutover_authorized"] is False
    assert seen[0][0]["options"] == "-c default_transaction_read_only=on"
    assert all(sql.startswith("SELECT ") for sql, _ in raw.queries)
    assert raw.closed and not seen[0][1].exists()
    assert "NEVER_PRINT_TEST_SECRET" not in json.dumps(report)


def test_prepare_without_full_approval_never_connects():
    def forbidden(*args, **kwargs):
        raise AssertionError("Network attempted before operator approval")
    opts = _prepare_kwargs()
    for key in (
        "confirmation", "snapshot_attested", "external_backup_restore_attested",
        "isolated_target_attested", "free_plan_capacity_attested",
    ):
        invalid = opts.copy()
        invalid[key] = "" if key == "confirmation" else False
        with pytest.raises(
            operator.AivenStagingOperatorError,
            match="PG_STAGING_OPERATOR_GATES_REQUIRED",
        ):
            operator.inspect_or_prepare(**invalid, connect=forbidden)


def test_invalid_dsn_rejected_without_network():
    def forbidden(*args, **kwargs):
        raise AssertionError("Network forbidden")
    with pytest.raises(
        operator.AivenStagingOperatorError,
        match="PG_STAGING_CONFIGURATION_INVALID",
    ):
        operator.inspect_or_prepare(
            mode="inspect", dsn=_DSN.replace("aivencloud.com", "example.invalid"),
            ca_pem=_CA, connect=forbidden, **_PIN,
        )


@pytest.mark.parametrize("schemas,tables", [(1, 0), (0, 1), (1, 2)])
def test_existing_objects_never_run_ddl(schemas, tables):
    raw = FakeRaw(schemas=schemas, tables=tables)
    with pytest.raises(
        operator.AivenStagingOperatorError,
        match="PG_STAGING_DESTINATION_NOT_EMPTY",
    ):
        operator.inspect_or_prepare(
            **_prepare_kwargs(), connect=_fake_connector(raw, []),
        )
    assert raw.closed
    assert len(raw.queries) == 3


def test_capacity_review_blocks_before_writes():
    raw = FakeRaw(bytes_used=600 * 1024 * 1024)
    with pytest.raises(
        operator.AivenStagingOperatorError,
        match="PG_STAGING_CAPACITY_REVIEW_REQUIRED",
    ):
        operator.inspect_or_prepare(
            **_prepare_kwargs(), connect=_fake_connector(raw, []),
        )
    assert len(raw.queries) == 3


def test_one_shot_prepare_only_after_all_gates_and_pinned_tls(monkeypatch):
    raw = FakeRaw()
    seen, calls = [], []

    def prepare(connection, policy, *, confirmation):
        calls.append((connection, policy, confirmation))
        return {
            "status": "empty_staging_schema_prepared",
            "postcommit_readonly_preflight": "preimport_structural_pass",
            "private_import_performed": False,
        }

    monkeypatch.setattr(operator, "prepare_empty_staging_schema", prepare)
    report = operator.inspect_or_prepare(
        **_prepare_kwargs(), connect=_fake_connector(raw, seen),
    )
    assert report["status"] == "empty_staging_schema_prepared"
    assert report["operator_attested_backup_restore"] is True
    assert report["capacity_independently_verified"] is False
    assert report["core_storage_switched"] is False
    assert report["production_cutover_authorized"] is False
    assert len(calls) == 1
    assert calls[0][0] is raw
    assert calls[0][1].host == "synthetic-project.f.aivencloud.com"
    assert calls[0][1].database == "defaultdb"
    assert calls[0][1].require_tls is True
    assert calls[0][2] == "PREPARE_EMPTY_PG18_ISOLATED_STAGING_ONLY"
    assert seen[0][0]["options"] == "-c default_transaction_read_only=off"
    assert raw.closed and not seen[0][1].exists()


def test_main_missing_config_fails_closed_and_never_runs_ddl(monkeypatch, capsys):
    monkeypatch.delenv("AMORA_POSTGRES_DSN", raising=False)
    monkeypatch.delenv("AMORA_POSTGRES_CA_FILE", raising=False)
    monkeypatch.delenv("AMORA_POSTGRES_CA_PEM", raising=False)
    assert operator.main(["inspect"]) == 2
    out = capsys.readouterr().out
    assert out.startswith("AIVEN_STAGING_OPERATOR ")
    payload = json.loads(out.split(" ", 1)[1])
    assert payload["status"] == "blocked"
    assert payload["reason"] == "PG_STAGING_CONFIGURATION_INVALID"
    assert payload["aiven_import_performed"] is False


def test_connection_exception_is_not_logged_or_exposed(capsys):
    def broken(*args, **kwargs):
        raise RuntimeError(_DSN)
    with pytest.raises(
        operator.AivenStagingOperatorError,
        match="PG_STAGING_CONNECTION_FAILED",
    ) as exc:
        operator.inspect_or_prepare(
            mode="inspect", dsn=_DSN, ca_pem=_CA, connect=broken,
            **_PIN,
        )
    assert exc.value.__cause__ is None
    assert "NEVER_PRINT_TEST_SECRET" not in str(exc.value)


def test_expected_endpoint_pin_required_before_network():
    def forbidden(*args, **kwargs):
        raise AssertionError("Must never connect to unpinned endpoint")
    with pytest.raises(
        operator.AivenStagingOperatorError,
        match="PG_STAGING_ENDPOINT_PIN_REQUIRED",
    ):
        operator.inspect_or_prepare(
            mode="inspect", dsn=_DSN, ca_pem=_CA, connect=forbidden,
        )
    with pytest.raises(
        operator.AivenStagingOperatorError,
        match="PG_STAGING_ENDPOINT_PIN_MISMATCH",
    ):
        operator.inspect_or_prepare(
            mode="inspect", dsn=_DSN, ca_pem=_CA, connect=forbidden,
            **{**_PIN, "expected_port": 9999},
        )


def test_readonly_verify_preimport_structure_without_recreating_schema(monkeypatch):
    raw = FakeRaw(schemas=1, tables=53)
    seen = []
    monkeypatch.setattr(
        operator, "inspect_prepared_postgres",
        lambda conn, policy, **kw: type("Result", (), {
            "status": "preimport_structural_pass",
        })(),
    )
    report = operator.inspect_or_prepare(
        mode="verify", dsn=_DSN, ca_pem=_CA,
        connect=_fake_connector(raw, seen), **_PIN,
    )
    assert report["status"] == "staging_structure_verified"
    assert report["structural_state"] == "preimport_structural_pass"
    assert report["private_row_parity_independently_verified"] is False
    assert report["aiven_import_performed"] is False
    assert report["production_cutover_authorized"] is False
    assert seen[0][0]["options"] == "-c default_transaction_read_only=on"
    assert all(sql.startswith("SELECT ") for sql, _ in raw.queries)
    assert raw.closed


def test_readonly_verify_rejects_unreviewed_structure(monkeypatch):
    raw = FakeRaw(schemas=1, tables=53)
    monkeypatch.setattr(
        operator, "inspect_prepared_postgres",
        lambda conn, policy, **kw: type("Result", (), {
            "status": "blocked",
        })(),
    )
    with pytest.raises(
        operator.AivenStagingOperatorError,
        match="PG_STAGING_STRUCTURE_NOT_VERIFIED",
    ):
        operator.inspect_or_prepare(
            mode="verify", dsn=_DSN, ca_pem=_CA,
            connect=_fake_connector(raw, []), **_PIN,
        )
