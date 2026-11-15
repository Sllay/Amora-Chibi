"""PCS.4C: private snapshot certification and isolated restore safety."""
import json
import sqlite3
import zipfile

import httpx
import pytest

from amora.app import AmoraCore
from amora.events import PrivacyScope
from amora.storage import pcs4_recovery as recovery
from amora.storage.remote import LibSQLBackend
from remote_storage_contracts_v13_source import HranaSQLite, remote
from test_memory_v03 import important_candidate
from test_messaging_lifecycle_stability import _clock


@pytest.fixture
def backup(remote, tmp_path, monkeypatch):
    backend, server = remote
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "source-runtime"))
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    core = AmoraCore.bootstrap(storage=backend, clock=_clock())
    try:
        assert core.memory.remember(important_candidate(
            content="Synthetic private recovery evidence", privacy_scope=PrivacyScope.PRIVATE,
            owner_entity_id="PERSON_BK",
        )).memory
        core.avatar.store.put_asset("pcs4c-asset", "image/png", b"fixture")
        path = core.create_backup("pcs4c.db")
    finally:
        core.close()
    assert not server.streams
    return path


def test_verify_and_restore_exact_roundtrip(backup, tmp_path):
    target_server = HranaSQLite(tmp_path / "isolated.db")
    backend = LibSQLBackend("https://restore.example", "test-only",
                            transport=httpx.MockTransport(target_server))
    before = recovery.verify_snapshot(backup)
    assert before["assets"] >= 1
    report = recovery.certify_restore(backup, backend)
    assert report["equal_tables"] == before["tables"]
    assert report["database_sha256"] == before["database_sha256"]
    assert not target_server.streams
    with pytest.raises(ValueError, match="not empty"):
        recovery.certify_restore(backup, backend)
    assert not target_server.streams


def test_sidecar_mismatch_detected_before_restore(backup):
    archive = str(backup) + ".avatar.zip"
    with zipfile.ZipFile(archive, "w") as z:
        z.writestr("manifest.json", json.dumps({"version": 1, "assets": []}))
    with pytest.raises(ValueError, match="PCS4C_ASSET_MANIFEST"):
        recovery.verify_snapshot(backup)


def test_changed_rows_detected(backup, tmp_path):
    changed = tmp_path / "changed.db"
    changed.write_bytes(backup.read_bytes())
    with sqlite3.connect(changed) as conn:
        conn.execute("UPDATE memory_records SET access_count=access_count+1")
    with pytest.raises(ValueError, match="PCS4C_DATA_MISMATCH"):
        recovery.compare_snapshots(backup, changed)


def test_destination_requires_distinct_host_and_token(monkeypatch):
    monkeypatch.setenv("AMORA_LIBSQL_URL", "libsql://source.example")
    monkeypatch.setenv("AMORA_PCS4C_DESTINATION_URL", "https://SOURCE.example/")
    monkeypatch.setenv("AMORA_PCS4C_DESTINATION_TOKEN", "private-destination")
    monkeypatch.setenv("AMORA_SECRET_LIBSQL_AUTH_TOKEN", "private-source")
    with pytest.raises(ValueError, match="PCS4C_DESTINATION_IS_SOURCE"):
        recovery.destination_from_environment()
    monkeypatch.setenv("AMORA_PCS4C_DESTINATION_URL", "https://other.example")
    monkeypatch.setenv("AMORA_PCS4C_DESTINATION_TOKEN", "private-source")
    with pytest.raises(ValueError, match="PCS4C_DESTINATION_TOKEN_REUSED"):
        recovery.destination_from_environment()
    monkeypatch.setenv("AMORA_PCS4C_DESTINATION_TOKEN", "private-destination")
    assert recovery.destination_from_environment().url == "https://other.example"


def test_cli_errors_do_not_print_secret_or_path(tmp_path, monkeypatch, capsys):
    def fail(path):
        raise RuntimeError("private-token")
    monkeypatch.setattr(recovery, "verify_snapshot", fail)
    assert recovery.main(["verify", str(tmp_path / "private-path.db")]) == 1
    output = capsys.readouterr().out
    assert "private-token" not in output and "private-path" not in output
    assert json.loads(output.split(" ", 1)[1])["status"] == "error"
    assert recovery.main(["restore", str(tmp_path / "private-path.db")]) == 1
    assert "PCS4C_CONFIRM_ISOLATION" in capsys.readouterr().out
