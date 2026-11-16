"""C.2C.1 — isolated v18→v19, exact restore and restart certification."""
from contextlib import closing
from datetime import timedelta
from pathlib import Path
import shutil
import sqlite3

import httpx
import pytest

from amora.storage import LATEST_SCHEMA_VERSION
from amora.storage import pcs4_recovery as recovery
from amora.storage import pcs4_restart as restart
from amora.storage import schema19_drill as drill
from amora.storage.backend import SQLiteBackend
from amora.storage.remote import LibSQLBackend
from remote_storage_contracts_v13_source import HranaSQLite, remote
from test_pcs4_recovery import backup
from test_multimodal_episodic_mm00 import open_stores, selected
from test_multimodal_episode_work_mm00 import repository, add_event
from amora.memory.episode_work import EnqueueResult


def target_backend(path):
    server = HranaSQLite(path)
    return LibSQLBackend("https://schema19-restore.example", "isolated-test",
                         transport=httpx.MockTransport(server)), server


def test_upgrade_preserves_v18_exactly_and_restore_v19_requires_opt_in(backup, tmp_path):
    original = recovery.verify_snapshot(backup)
    target, report = drill.prepare_schema19_copy(backup, tmp_path / "candidate")
    assert LATEST_SCHEMA_VERSION == 18
    assert report["source_schema_version"] == 18
    assert report["schema_version"] == 19
    assert report["old_tables"] == original["tables"]
    assert report["preserved_tables"] == original["tables"] - 1
    assert report["new_tables"] == 3
    assert report["tables"] == original["tables"] + 3
    assert report["rows"] == original["rows"] + 1
    assert report["archive_sha256"] == original["archive_sha256"]
    assert report["assets"] == original["assets"] >= 1
    assert recovery.verify_snapshot(backup) == original
    with pytest.raises(ValueError, match="PCS4C_SCHEMA"):
        recovery.verify_snapshot(target)
    backend, server = target_backend(tmp_path / "restored.db")
    with pytest.raises(ValueError, match="PCS4C_SCHEMA"):
        recovery.certify_restore(target, backend)
    assert not server.path.exists()
    with pytest.raises(ValueError, match="PCS4C_SCHEMA"):
        recovery.certify_restore(backup, backend, expected_schema=19)
    assert not server.path.exists()
    restored = recovery.certify_restore(target, backend, expected_schema=19)
    assert restored["equal_tables"] == report["tables"]
    assert restored["database_sha256"] == report["database_sha256"]
    assert not server.streams
    with pytest.raises(ValueError, match="not empty"):
        recovery.certify_restore(target, backend, expected_schema=19)
    assert recovery.verify_snapshot(backup) == original


def test_upgrade_rejects_existing_output_and_cleans_failed_copy(backup, tmp_path, monkeypatch):
    existing = tmp_path / "existing"
    existing.mkdir()
    marker = existing / "keep.txt"
    marker.write_text("preserve")
    with pytest.raises(FileExistsError):
        drill.prepare_schema19_copy(backup, existing)
    assert marker.read_text() == "preserve"
    original = recovery.verify_snapshot(backup)
    output = tmp_path / "failed"
    monkeypatch.setattr(drill, "SENSORY_EPISODE_QUEUE_CANDIDATE_SQL",
                        drill.SENSORY_EPISODE_QUEUE_CANDIDATE_SQL + "\nINVALID SQL;")
    with pytest.raises(sqlite3.DatabaseError):
        drill.prepare_schema19_copy(backup, output)
    assert not output.exists()
    assert recovery.verify_snapshot(backup) == original


def test_compare_upgrade_rejects_changed_old_data_and_history(backup, tmp_path):
    target, _ = drill.prepare_schema19_copy(backup, tmp_path / "candidate")
    with closing(sqlite3.connect(target)) as conn, conn:
        conn.execute("UPDATE memory_records SET access_count=access_count+1")
    with pytest.raises(ValueError, match="SCHEMA19_DATA_MISMATCH"):
        drill.compare_upgrade(backup, target)
    target, _ = drill.prepare_schema19_copy(backup, tmp_path / "candidate2")
    with closing(sqlite3.connect(target)) as conn, conn:
        conn.execute("UPDATE schema_migrations SET applied_at='changed' WHERE version=18")
    with pytest.raises(ValueError, match="SCHEMA19_HISTORY_MISMATCH"):
        drill.compare_upgrade(backup, target)


def test_v19_restore_preserves_work_and_core_rank_on_two_boots(backup, tmp_path, monkeypatch):
    target, _ = drill.prepare_schema19_copy(backup, tmp_path / "candidate")
    # Populate only the isolated candidate, after the additive comparison.
    db, memory = open_stores(target)
    try:
        work = repository(db, memory, lease_seconds=5)
        one = add_event(db, 301)
        two = add_event(db, 302)
        assert work.enqueue(selected(one)) == EnqueueResult.ACCEPTED
        done = work.claim()
        assert work.process(done).memory.id == done.job_id
        assert work.enqueue(selected(two)) == EnqueueResult.ACCEPTED
        pending = work.claim()
        assert work.state(pending.job_id)["status"] == "claimed"
        assert db.schema_version() == 19
    finally:
        memory.close()
        db.close()

    backend, server = target_backend(tmp_path / "restored.db")
    verified = recovery.verify_snapshot(target, expected_schema=19)
    assert recovery.certify_restore(target, backend, expected_schema=19)["equal_tables"] == verified["tables"]
    boots = []

    def boot(home, timestamp, backend=None, *, schema_version=18):
        with monkeypatch.context() as env:
            env.setenv("AMORA_HOME", str(home))
            env.setenv("AMORA_MODE", "local")
            env.setenv("AMORA_ASSET_STORAGE", "database")
            if schema_version == 19:
                env.setenv("AMORA_RUNTIME_SCHEMA_TARGET", "19")
                env.setenv("AMORA_RUNTIME_SCHEMA_CONFIRMATION", "CERTIFIED_V19_BACKUP_AND_ROLLBACK")
            boots.append(backend is not None)
            return restart._boot(backend or SQLiteBackend(), timestamp)

    monkeypatch.setattr(restart, "_run_boot", boot)
    report = restart.certify_restart(target, backend, expected_schema=19)
    assert boots == [False, True, True]
    assert report["boots"] == 2 and report["rank_equal"] is True
    assert report["schema_version"] == 19
    assert report["memory_tables_equal"] == 4
    assert report["equal_tables"] == verified["tables"]
    # A boot does not consume work. The expired claim can be reclaimed explicitly.
    db, memory = open_stores(tmp_path / "reopen.db", backend)
    try:
        work = repository(db, memory, lease_seconds=5)
        assert work.state(done.job_id)["status"] == "done"
        memory.clock.advance(timedelta(seconds=5))
        recovered = work.claim()
        assert recovered.job_id == pending.job_id and recovered.attempts == 2
        assert work.process(recovered).memory.id == pending.job_id
    finally:
        memory.close()
        db.close()
    assert not server.streams
    assert recovery.verify_snapshot(backup)["schema_version"] == 18


def test_mislabeled_snapshot_and_missing_candidate_index_fail_closed(backup, tmp_path):
    fake = tmp_path / "fake.db"
    shutil.copyfile(backup, fake)
    shutil.copyfile(str(backup) + ".avatar.zip", str(fake) + ".avatar.zip")
    with closing(sqlite3.connect(fake)) as conn, conn:
        conn.execute("INSERT INTO schema_migrations(version) VALUES (19)")
    with pytest.raises(ValueError, match="SCHEMA19_DDL_MISMATCH"):
        recovery.verify_snapshot(fake, expected_schema=19)
    target, _ = drill.prepare_schema19_copy(backup, tmp_path / "candidate")
    with closing(sqlite3.connect(target)) as conn, conn:
        conn.execute("DROP INDEX idx_sensory_episode_due")
    with pytest.raises(ValueError, match="SCHEMA19_DDL_MISMATCH"):
        recovery.verify_snapshot(target, expected_schema=19)


def test_cli_requires_isolation_and_sanitizes_failure(backup, tmp_path, monkeypatch, capsys):
    import json

    output = tmp_path / "cli-copy"
    assert drill.main(["prepare", str(backup), str(output)]) == 0
    report = json.loads(capsys.readouterr().out.split(" ", 1)[1])
    assert report["schema_version"] == 19 and report["runtime_schema"] == 18
    assert "database_sha256" not in report and "archive_sha256" not in report
    with pytest.raises(SystemExit):
        drill.main(["restore", str(output / "schema19.db")])
    capsys.readouterr()
    def fail(*args, **kwargs):
        raise RuntimeError("private-token")
    monkeypatch.setattr(drill, "verify_snapshot", fail)
    assert drill.main(["verify", "/private-path.db"]) == 1
    logs = capsys.readouterr().out
    assert "private-token" not in logs and "private-path" not in logs
