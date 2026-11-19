"""Safety contracts for native PostgreSQL 18 backup operator. No network."""
from __future__ import annotations

import json

import pytest

import importlib.util
from pathlib import Path

_SOURCE = Path(__file__).resolve().parents[1] / "tools/ops/aiven_pg18_native_backup.py"
_SPEC = importlib.util.spec_from_file_location("aiven_pg18_native_backup", _SOURCE)
assert _SPEC and _SPEC.loader
backup = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(backup)


def _env(monkeypatch, tmp_path):
    ca = tmp_path / "aiven-ca.pem"
    ca.write_text("-----BEGIN CERTIFICATE-----\n" + "A" * 1000 +
                  "\n-----END CERTIFICATE-----\n")
    monkeypatch.setenv(
        "AMORA_POSTGRES_DSN",
        "postgresql://amora-user:test-backup-password@fake.aivencloud.com:19987/defaultdb",
    )
    monkeypatch.setenv("AMORA_POSTGRES_CA_FILE", str(ca))
    monkeypatch.delenv("AMORA_POSTGRES_CA_PEM", raising=False)
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_HOST", "fake.aivencloud.com")
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_PORT", "19987")
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_DATABASE", "defaultdb")


def test_native_dump_settings_preserve_credentials_only_in_pg_env(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    env = backup._environment()
    assert env["PGHOST"] == "fake.aivencloud.com"
    assert env["PGPORT"] == "19987"
    assert env["PGDATABASE"] == "defaultdb"
    assert env["PGUSER"] == "amora-user"
    assert env["PGPASSWORD"] == "test-backup-password"
    assert env["PGSSLMODE"] == "verify-full"
    assert env["PGOPTIONS"].endswith("default_transaction_read_only=on")


def test_native_dump_blocks_wrong_endpoint_and_unmounted_ca(monkeypatch, tmp_path):
    _env(monkeypatch, tmp_path)
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_DATABASE", "otherdb")
    with pytest.raises(backup.BackupBlocked, match="PG_BACKUP_TARGET_OR_CA_INVALID"):
        backup._environment()
    monkeypatch.setenv("AMORA_STAGING_EXPECTED_DATABASE", "defaultdb")
    monkeypatch.setenv("AMORA_POSTGRES_CA_FILE", "/missing/certificate.pem")
    with pytest.raises(backup.BackupBlocked, match="PG_BACKUP_TARGET_OR_CA_INVALID"):
        backup._environment()


def test_cli_requires_confirmation_without_touching_volume(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["aiven_pg18_native_backup.py"])
    assert backup.main() == 2
    log = capsys.readouterr().out
    result = json.loads(log.split("AIVEN_NATIVE_BACKUP ", 1)[1])
    assert result == {
        "status": "blocked",
        "reason": "PG_BACKUP_OPERATOR_CONFIRMATION_REQUIRED",
        "independent_restore_verified": False,
        "offsite_backup_verified": False,
        "core_storage_switched": False,
        "production_cutover_authorized": False,
    }


def test_native_dump_rejects_nonmounted_volume_even_if_operator_confirmed(
    monkeypatch, tmp_path,
):
    _env(monkeypatch, tmp_path)
    monkeypatch.setattr(backup, "ARCHIVE", tmp_path / "not-private" / "backup.dump")
    with pytest.raises(backup.BackupBlocked, match="PG_BACKUP_DURABLE_MOUNT_UNAVAILABLE"):
        backup.create_backup()
