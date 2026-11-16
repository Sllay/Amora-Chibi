"""C.2C.2: existing recovery job, separate v19 state on the certified test destination."""
import json
import shutil

import httpx
import pytest

from amora.storage import pcs4_recovery as recovery, pcs4_restart as restart
from amora.storage.backend import SQLiteBackend
from amora.storage.pcs4_contract import CONFIRMATIONS, MARKER, STEPS
from amora.storage.pcs4_dev import _safe_report
from amora.storage.pcs4_web import RecoveryPanel, _origin_digest, _atomic_json
from amora.storage.remote import LibSQLBackend
from amora.storage.schema19_operator import run_schema19
from amora.storage.schema19_migration import BASELINE_NAME, migrate_certified_destination
from remote_storage_contracts_v13_source import HranaSQLite, remote
from test_pcs4_recovery import backup
from test_pcs4_dev import FakeNorthflank, controller


@pytest.fixture
def operator(backup, remote, tmp_path):
    source, source_server = remote
    former_server = HranaSQLite(tmp_path / "former.db")
    former = LibSQLBackend("https://old-destination.example", "former-token",
                           transport=httpx.MockTransport(former_server))
    panel = RecoveryPanel(tmp_path / "private" / "pcs4c", source, destination=lambda: former)
    panel.snapshot.parent.mkdir()
    shutil.copyfile(backup, panel.snapshot)
    shutil.copyfile(str(backup) + ".avatar.zip", str(panel.snapshot) + ".avatar.zip")
    report = recovery.verify_snapshot(panel.snapshot)
    base = {"status": "ok", **{key: report[key] for key in ("schema_version", "tables", "rows", "assets")}}
    old = {
        "status": "ok", "running": None, "completed": list(STEPS),
        "source_id": panel.source_id, "destination_id": _origin_digest(former),
        "database_sha256": report["database_sha256"], "archive_sha256": report["archive_sha256"],
        "reports": {"backup": base, "verify": base, "restore": dict(base, equal_tables=base["tables"]),
                    "restart": {"status": "ok", "schema_version": 18, "tables": base["tables"],
                                "equal_tables": base["tables"], "boots": 2, "query_count": 5,
                                "memory_tables_equal": 4, "rank_equal": True, "hit_counts": [1, 1, 1, 1, 1]}},
    }
    panel._write(old)
    recovery.certify_restore(panel.snapshot, former, expected_schema=18)
    former_server.calls.clear()
    return panel, former, former_server, source_server


def action(panel, target, name):
    assert target is panel.destination()
    return run_schema19(panel, name)


def test_full_operator_drill_preserves_old_state_and_both_databases(operator, monkeypatch):
    panel, target, server, source_server = operator
    previous = panel.state_path.read_bytes()
    old_report = recovery.verify_snapshot(panel.snapshot)
    source_calls = len(source_server.calls)
    boots = []

    def boot(home, timestamp, backend=None):
        with monkeypatch.context() as env:
            env.setenv("AMORA_HOME", str(home))
            env.setenv("AMORA_MODE", "local")
            env.setenv("AMORA_ASSET_STORAGE", "database")
            boots.append(backend is not None)
            return restart._boot(backend or SQLiteBackend(), timestamp)

    monkeypatch.setattr(restart, "_run_boot", boot)
    # Replicate the already completed PCS.4C restart, not just an empty restore.
    prior = restart.certify_restart(panel.snapshot, target, expected_schema=18)
    assert prior["rank_equal"] is True
    assert boots == [False, True, True]
    boots.clear()
    server.calls.clear()
    with pytest.raises(ValueError, match="STEP_BLOCKED"):
        action(panel, target, "schema19_migrate")
    prepared = action(panel, target, "schema19_prepare")
    assert _safe_report(prepared)["next"] == "schema19_migrate"
    assert prepared["schema19"]["reports"]["schema19_prepare"]["tables"] == old_report["tables"] + 3
    assert prepared["schema19"]["reports"]["schema19_prepare"]["relationship_sequence_delta"] > 0
    restored = action(panel, target, "schema19_migrate")
    migration_calls = list(server.calls)
    assert _safe_report(restored)["next"] == "schema19_restart"
    certified = action(panel, target, "schema19_restart")
    safe = _safe_report(certified)
    assert safe["next"] is None
    assert safe["reports"]["schema19_restart"]["episode_work_tables_equal"] == 3
    assert boots == [False, True, True]
    assert _safe_report(action(panel, target, "schema19_status")) == safe
    assert panel.state_path.read_bytes() == previous
    assert recovery.verify_snapshot(panel.snapshot) == old_report
    assert len(source_server.calls) == source_calls
    assert not server.streams
    assert not any(sql.lstrip().upper().startswith(('DELETE ', 'DROP ', 'UPDATE ')) for sql in migration_calls)


def test_interrupted_migration_review_v18_then_exact(operator):
    panel, target, server, _ = operator
    action(panel, target, "schema19_prepare")
    path = panel.root / "schema19" / "state.json"
    state = json.loads(path.read_text())
    state.update(status="running", failed_action="schema19_migrate")
    _atomic_json(path, state)
    assert _safe_report(action(panel, target, "schema19_status"))["next"] == "schema19_review_migration"
    reviewed = action(panel, target, "schema19_review_migration")
    assert _safe_report(reviewed)["next"] == "schema19_migrate"
    assert reviewed['schema19']['review_outcome'] == 'v18_exact'
    # Simulate committed migration with lost state update.
    candidate = panel.root / "schema19" / "candidate" / "schema19.db"
    migrate_certified_destination(candidate.parent / BASELINE_NAME, candidate, target)
    state = json.loads(path.read_text())
    state.update(status="running", failed_action="schema19_migrate", review_outcome=None)
    _atomic_json(path, state)
    exact = action(panel, target, "schema19_review_migration")
    assert _safe_report(exact)["next"] == "schema19_restart"
    assert exact["schema19"]["review_outcome"] == "exact"
    assert not server.streams


def test_wrong_target_incomplete_original_and_changed_pin_are_blocked(operator):
    panel, target, server, _ = operator
    old = panel._read()
    panel.destination = lambda: panel.source
    panel._write(dict(old, destination_id=panel.source_id))
    with pytest.raises(ValueError, match="DESTINATION_NOT_INDEPENDENT"):
        run_schema19(panel, "schema19_prepare")
    panel.destination = lambda: target
    panel._write(old)
    old = panel._read()
    panel._write(dict(old, completed=list(STEPS[:-1])))
    with pytest.raises(ValueError, match="V18_NOT_CERTIFIED"):
        action(panel, target, "schema19_prepare")
    panel._write(old)
    action(panel, target, "schema19_prepare")
    calls = len(server.calls)
    other = LibSQLBackend("https://different.example", "other-token",
                         transport=httpx.MockTransport(server))
    panel.destination = lambda: other
    with pytest.raises(ValueError, match="V18_DESTINATION_CHANGED"):
        run_schema19(panel, "schema19_migrate")
    panel.destination = lambda: target
    assert len(server.calls) == calls
    # Migration is refused if the candidate bytes have changed.
    candidate = panel.root / "schema19" / "candidate" / "schema19.db"
    with candidate.open("ab") as file:
        file.write(b"changed")
    failed = action(panel, target, "schema19_migrate")
    assert _safe_report(failed)["next"] == "schema19_review_migration"
    assert len(server.calls) == calls


def test_controller_recovers_v19_error_and_filters_report(operator):
    panel, target, _, _ = operator
    prepared = action(panel, target, "schema19_prepare")
    prepared["schema19"]["reports"]["schema19_prepare"]["database_sha256"] = "private"
    assert "private" not in json.dumps(_safe_report(prepared))
    fake = FakeNorthflank()
    fake.runs = [{"id": "run-1", "status": "SUCCESS", "active": 0, "concluded": True,
                  "startedAt": "2026-11-15T01:00:00Z", "concludedAt": "2026-11-15T01:01:00Z"}]
    fake.report = prepared
    control = controller(fake)
    assert control.status()["next"] == "schema19_migrate"
    fake.runs[0]["status"] = "FAILED"
    fake.report = {**prepared, "action": "schema19_migrate",
                   "schema19": {**prepared["schema19"], "status": "error",
                                "failed_action": "schema19_migrate"}}
    assert control.status()["next"] == "schema19_review_migration"
    fake.report["schema19"]["reports"]["schema19_prepare"]["rows"] += 1
    assert control.status()["status"] == "blocked"


def test_old_certified_report_enables_new_prepare_without_rewriting_old_state(operator):
    panel, target, _, _ = operator
    original = {"status": "ok", "action": "restart", "running": None,
                "completed": list(STEPS), "reports": panel._read()["reports"]}
    assert _safe_report(original)["next"] == "schema19_prepare"
    assert CONFIRMATIONS["schema19_migrate"] == "MIGRAR TESTE V18 PARA V19"


def test_controller_submits_only_explicit_v19_action_and_recovers_missing_report(operator):
    panel, target, _, _ = operator
    class V19Northflank(FakeNorthflank):
        def __call__(self, request):
            if request.method == "POST":
                payload = json.loads(request.content)
                assert payload == {"runtimeEnvironment": {"AMORA_PCS4_JOB_ACTION": "schema19_migrate"}}
                self.calls.append((request.method, request.url.path))
                self.runs = [{"id": "run-1", "status": "RUNNING", "active": 1, "concluded": False,
                              "startedAt": "2026-11-15T01:00:00Z"}]
                return httpx.Response(200, json={"data": {"id": "run-1"}})
            return super().__call__(request)
    fake = V19Northflank()
    fake.runs = [{"id": "run-1", "status": "SUCCESS", "active": 0, "concluded": True,
                  "startedAt": "2026-11-15T01:00:00Z", "concludedAt": "2026-11-15T01:01:00Z"}]
    fake.report = action(panel, target, "schema19_prepare")
    control = controller(fake)
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        control.start("schema19_restart", CONFIRMATIONS["schema19_restart"])
    with pytest.raises(HTTPException):
        control.start("schema19_migrate", "wrong")
    assert control.start("schema19_migrate", CONFIRMATIONS["schema19_migrate"]) == {"status": "running"}
    assert control.status()["status"] == "running"
    fake.runs[0].update(status="FAILED", active=0, concluded=True, concludedAt="2026-11-15T01:01:00Z")
    fake.report = {"status": "error", "action": "schema19_migrate",
                   "reason": "SCHEMA19_CONFIGURATION_REQUIRED", "private": "secret"}
    report = control.status()
    assert report["next"] == "schema19_status"
    assert "secret" not in json.dumps(report)
    fake.report["reason"] = "secret"
    assert control.status()["status"] == "blocked"


def test_shared_lock_and_interrupted_restart_do_not_run_again(operator):
    panel, target, server, _ = operator
    fd = panel._lock()
    import os
    try:
        with pytest.raises(BlockingIOError):
            action(panel, target, "schema19_prepare")
    finally:
        os.close(fd)
    action(panel, target, "schema19_prepare")
    action(panel, target, "schema19_migrate")
    path = panel.root / "schema19" / "state.json"
    state = json.loads(path.read_text())
    state.update(status="running", failed_action="schema19_restart")
    _atomic_json(path, state)
    calls = len(server.calls)
    result = _safe_report(action(panel, target, "schema19_status"))
    assert result["status"] == "error" and result["next"] is None
    with pytest.raises(ValueError, match="STEP_BLOCKED"):
        action(panel, target, "schema19_restart")
    assert len(server.calls) == calls


def test_migration_divergent_target_blocks_without_overwrite(operator):
    import sqlite3
    panel, target, server, _ = operator
    action(panel, target, "schema19_prepare")
    with sqlite3.connect(server.path) as db:
        db.execute("INSERT INTO schema_migrations(version) VALUES (999)")
    failed = action(panel, target, "schema19_migrate")
    assert _safe_report(failed)["next"] == "schema19_review_migration"
    result = action(panel, target, "schema19_review_migration")
    assert result["schema19"]["status"] == "error"
    assert _safe_report(result)["next"] == "schema19_review_migration"
    with sqlite3.connect(server.path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 999
        assert db.execute("SELECT COUNT(*) FROM sqlite_schema WHERE name='sensory_episode_jobs'").fetchone()[0] == 0
    assert not server.streams


def test_migration_failure_rolls_back_ddl_and_history(operator, monkeypatch):
    import sqlite3
    from amora.storage import schema19_migration as migration
    panel, target, server, _ = operator
    action(panel, target, "schema19_prepare")
    original_ddl = migration.SENSORY_EPISODE_QUEUE_CANDIDATE_SQL
    monkeypatch.setattr(migration, "SENSORY_EPISODE_QUEUE_CANDIDATE_SQL",
                        original_ddl + "\nINSERT INTO nonexistent_table VALUES (1);")
    failed = action(panel, target, "schema19_migrate")
    assert failed["schema19"]["status"] == "error"
    assert any("CREATE TABLE" in sql.upper() and "sensory_episode_jobs" in sql for sql in server.calls)
    assert any(sql.strip().upper() == "ROLLBACK" for sql in server.calls)
    with sqlite3.connect(server.path) as db:
        assert db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 18
        assert db.execute("SELECT COUNT(*) FROM sqlite_schema WHERE name='sensory_episode_jobs'").fetchone()[0] == 0
    reviewed = action(panel, target, "schema19_review_migration")
    assert reviewed["schema19"]["review_outcome"] == "v18_exact"
    assert _safe_report(reviewed)["next"] == "schema19_migrate"
    monkeypatch.setattr(migration, "SENSORY_EPISODE_QUEUE_CANDIDATE_SQL", original_ddl)
    assert _safe_report(action(panel, target, "schema19_migrate"))["next"] == "schema19_restart"


def test_migration_refuses_empty_target(operator):
    import sqlite3
    panel, target, server, _ = operator
    action(panel, target, "schema19_prepare")
    server.path.unlink()
    failed = action(panel, target, "schema19_migrate")
    assert failed["schema19"]["status"] == "error"
    with sqlite3.connect(server.path) as db:
        assert db.execute("SELECT COUNT(*) FROM sqlite_schema").fetchone()[0] == 0
    assert action(panel, target, "schema19_review_migration")["schema19"]["status"] == "error"


@pytest.mark.parametrize("change", ["sequence_decrease", "data_change", "other_sequence"])
def test_prepare_rejects_unrecognized_drift_without_remote_writes(operator, change):
    import sqlite3
    panel, target, server, _ = operator
    with sqlite3.connect(server.path) as db:
        if change == "sequence_decrease":
            db.execute("UPDATE sqlite_sequence SET seq=seq-1 WHERE name='relationships'")
        elif change == "data_change":
            db.execute("UPDATE memory_records SET access_count=access_count+1")
        else:
            db.execute("INSERT INTO sqlite_sequence(name,seq) VALUES ('unknown',1)")
    result = action(panel, target, "schema19_prepare")
    assert result["schema19"]["status"] == "error"
    assert result["schema19"]["reason"] == "SCHEMA19_DESTINATION_DIVERGED"
    assert not (panel.root / "schema19" / "candidate").exists()
    assert all(sql.strip().upper().startswith(("SELECT ", "BEGIN", "ROLLBACK", "COMMIT"))
               for sql in server.calls)
    assert not server.streams
