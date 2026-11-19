"""Two existing Jobs; private PCS4C import operator, fake I/O only in unit tests."""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from amora.storage import aiven_private_import_operator as bridge
from amora.storage.postgres_private_staging_import import PrivateStagingImportError

_DSN = (
    "postgresql://avnadmin:NEVER_LOG_PRIVATE_SECRET@"
    "synthetic-project.f.aivencloud.com:14600/defaultdb?sslmode=require"
)
_CA = "-----BEGIN CERTIFICATE-----\nDUMMY_ONLY\n-----END CERTIFICATE-----"
_PIN = {
    "expected_host": "synthetic-project.f.aivencloud.com",
    "expected_port": 14600,
    "expected_database": "defaultdb",
}
_GOOD_SOURCE = {
    "status": "verified_offline",
    "schema_version": 18,
    "logical_tables": 51,
    "logical_rows": 48059,
    "avatar_assets": 0,
    "snapshot_bytes": 75563008,
}


def _source_args():
    return {
        "mount_ok": lambda path: path == Path("/private"),
        "audit_source": lambda snapshot, state: {
            "status": "ok", "schema_version": 18, "tables": 51,
            "rows": 48059, "assets": 0,
        },
        "inspect_source": lambda snapshot: dict(_GOOD_SOURCE),
    }


def _import_args():
    return {
        "mode": "import",
        "dsn": _DSN,
        "ca_pem": _CA,
        "confirmation": "IMPORT_VERIFIED_V18_TO_EMPTY_ISOLATED_STAGING",
        "source_freshness_attested": True,
        "external_backup_restore_attested": True,
        "isolated_target_attested": True,
        "free_plan_capacity_attested": True,
        **_PIN,
        **_source_args(),
    }


class Raw:
    closed = False
    autocommit = True
    def __init__(self):
        self.info = SimpleNamespace(
            host=_PIN["expected_host"], port=_PIN["expected_port"],
            dbname=_PIN["expected_database"], server_version=180003,
            get_parameters=lambda: {"sslmode": "verify-full"},
        )
        self.pgconn = SimpleNamespace(ssl_in_use=True)
    def close(self):
        self.closed = True


def _mock_connect(raw, seen):
    def connect(dsn, **kw):
        assert dsn == _DSN
        assert kw["sslmode"] == "verify-full"
        assert kw["autocommit"] is True
        assert kw["options"] == "-c default_transaction_read_only=off"
        assert kw["connect_timeout"] == 8
        cert = Path(kw["sslrootcert"])
        assert cert.read_text() == _CA
        assert cert.stat().st_mode & 0o077 == 0
        seen.append(cert)
        return raw
    return connect


def test_default_inspect_checks_source_offline_and_never_connects():
    def forbidden(*args, **kwargs):
        raise AssertionError("Any network access is forbidden")
    report = bridge.inspect_or_import(
        mode="inspect", connect=forbidden, **_source_args(),
    )
    assert report["status"] == "private_source_ready_for_staging"
    assert report["network_access"] is False
    assert report["logical_tables"] == 51
    assert report["logical_rows"] == 48059
    assert report["private_import_performed"] is False
    assert report["production_cutover_authorized"] is False
    assert "NEVER_LOG_PRIVATE_SECRET" not in json.dumps(report)


@pytest.mark.parametrize("missing", [
    "confirmation",
    "source_freshness_attested",
    "external_backup_restore_attested",
    "isolated_target_attested",
    "free_plan_capacity_attested",
])
def test_import_without_every_approval_does_not_touch_source_or_network(missing):
    def forbidden(*args, **kwargs):
        raise AssertionError("Neither disk nor network may be accessed")
    kw = _import_args()
    kw["mount_ok"] = forbidden
    kw["audit_source"] = forbidden
    kw["inspect_source"] = forbidden
    kw["connect"] = forbidden
    kw[missing] = "" if missing == "confirmation" else False
    with pytest.raises(
        bridge.PrivateBridgeError, match="PG_PRIVATE_OPERATOR_GATES_REQUIRED",
    ):
        bridge.inspect_or_import(**kw)


def test_missing_durable_mount_blocks_before_snapshot_or_network():
    def forbidden(*args, **kwargs):
        raise AssertionError("Should not touch source or socket")
    with pytest.raises(
        bridge.PrivateBridgeError, match="PG_PRIVATE_DURABLE_MOUNT_REQUIRED",
    ):
        bridge.inspect_or_import(
            mode="inspect",
            mount_ok=lambda path: False,
            audit_source=forbidden,
            inspect_source=forbidden,
            connect=forbidden,
        )


def test_drifted_backup_certification_fails_closed_without_connection():
    def forbidden(*args, **kwargs):
        raise AssertionError("No network allowed")
    source = _source_args()
    source["audit_source"] = lambda *args: {
        "status": "ok", "schema_version": 18, "tables": 51,
        "rows": 48060, "assets": 0,
    }
    with pytest.raises(
        bridge.PrivateBridgeError, match="PG_PRIVATE_SOURCE_CERTIFICATION_DRIFT",
    ):
        bridge.inspect_or_import(
            **{**_import_args(), **source, "connect": forbidden},
        )


def test_destination_pin_blocks_wrong_endpoint_without_connection():
    def forbidden(*args, **kwargs):
        raise AssertionError("No network allowed")
    with pytest.raises(
        bridge.PrivateBridgeError, match="PG_PRIVATE_ENDPOINT_PIN_MISMATCH",
    ):
        bridge.inspect_or_import(
            **{**_import_args(), "expected_port": 5000, "connect": forbidden},
        )


def test_one_shot_import_runs_only_after_full_gates_and_verified_tls():
    raw = Raw()
    seen = []
    calls = []
    def importer(snapshot, conn, target, *, confirmation):
        calls.append((snapshot, conn, target, confirmation))
        return {
            "status": "staged_import_completed",
            "relational_rows": 48040,
            "typed_row_parity": True,
            "production_cutover_authorized": False,
        }

    report = bridge.inspect_or_import(
        **_import_args(),
        connect=_mock_connect(raw, seen), import_target=importer,
    )
    assert report["status"] == "staged_import_completed"
    assert report["relational_rows"] == 48040
    assert report["operator_attested_external_backup_restore"] is True
    assert report["external_backup_restore_independently_verified"] is False
    assert report["production_cutover_authorized"] is False
    assert calls[0][0] == bridge.DEFAULT_SNAPSHOT
    assert calls[0][1] is raw
    assert calls[0][2].require_tls is True
    assert calls[0][2].schema == "amora_pg_v18_lab"
    assert calls[0][3] == "IMPORT_VERIFIED_V18_TO_EMPTY_ISOLATED_STAGING"
    assert raw.closed is True
    assert not seen[0].exists()
    assert "NEVER_LOG_PRIVATE_SECRET" not in json.dumps(report)


def test_import_failed_postconnect_never_exposes_error_and_requires_review():
    raw = Raw()
    def failure(*args, **kwargs):
        raise PrivateStagingImportError("PG_PRIVATE_IMPORT_FAILED_REVIEW_TARGET")
    with pytest.raises(
        bridge.PrivateBridgeError, match="PG_PRIVATE_IMPORT_FAILED_REVIEW_TARGET",
    ) as exc:
        bridge.inspect_or_import(
            **_import_args(), connect=_mock_connect(raw, []),
            import_target=failure,
        )
    assert exc.value.target_requires_review is True
    report = bridge._blocked(
        str(exc.value), target_requires_review=exc.value.target_requires_review,
    )
    assert report["target_requires_review"] is True
    assert report["private_import_performed"] is None


def test_main_offline_fails_closed_without_mounted_volume(monkeypatch, capsys):
    monkeypatch.setattr(bridge, "_mounted", lambda path: False)
    monkeypatch.delenv("AMORA_POSTGRES_DSN", raising=False)
    assert bridge.main(["inspect"]) == 2
    output = capsys.readouterr().out
    assert output.startswith("AIVEN_PRIVATE_BRIDGE ")
    report = json.loads(output.split(" ", 1)[1])
    assert report["status"] == "blocked"
    assert report["reason"] == "PG_PRIVATE_DURABLE_MOUNT_REQUIRED"
    assert report["private_import_performed"] is False


def test_main_import_without_confirmation_blocks_before_network(monkeypatch, capsys):
    monkeypatch.setattr(bridge, "_mounted", lambda path: False)
    assert bridge.main(["import"]) == 2
    report = json.loads(capsys.readouterr().out.split(" ", 1)[1])
    assert report["reason"] == "PG_PRIVATE_OPERATOR_GATES_REQUIRED"
    assert report["production_cutover_authorized"] is False
