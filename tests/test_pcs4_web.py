"""PCS.4C web operator: authenticated, durable, ordered, isolated recovery."""
import json
import time
from datetime import datetime, timezone

import httpx
import pytest
from fastapi.testclient import TestClient

from amora.storage import pcs4_web as web
from amora.storage import pcs4_restart as restart
from amora.storage.backend import SQLiteBackend
from amora.storage.remote import LibSQLBackend
from remote_storage_contracts_v13_source import HranaSQLite, remote
from test_pcs4_recovery import backup

TOKEN = "operator-only-test-token-32-characters"
AUTH = {"Authorization": "Bearer " + TOKEN}
CONFIRM = {
    "backup": "PAUSEI OS ESCRITORES",
    "verify": "VOLUME PRIVADO",
    "restore": "DESTINO VAZIO E INDEPENDENTE",
    "restart": "DESTINO ISOLADO",
}


def wait(client):
    for _ in range(500):
        state = client.get("/api/status", headers=AUTH).json()
        if state["status"] != "running":
            return state
        time.sleep(0.02)
    pytest.fail("web recovery did not finish")


def start(client, step):
    return client.post("/api/actions/" + step, headers=AUTH,
                       json={"confirmation": CONFIRM[step]})


def test_full_web_workflow(backup, remote, tmp_path, monkeypatch):
    source, source_server = remote
    target_server = HranaSQLite(tmp_path / "independent.db")
    target = LibSQLBackend("https://restore.example", "target-only",
                           transport=httpx.MockTransport(target_server))
    panel = web.RecoveryPanel(tmp_path / "private", source, lambda: target)
    # Keep actual Core boot with mock transport. Real subprocess isolation is
    # covered separately by test_pcs4_restart.
    def boot(home, timestamp, backend=None):
        with monkeypatch.context() as env:
            env.setenv("AMORA_HOME", str(home))
            env.setenv("AMORA_MODE", "local")
            env.setenv("AMORA_ASSET_STORAGE", "database")
            return restart._boot(backend or SQLiteBackend(), timestamp)
    monkeypatch.setattr(restart, "_run_boot", boot)
    with TestClient(web.create_app(panel, TOKEN)) as client:
        page = client.get("/")
        assert page.status_code == 200 and "Criar backup" in page.text
        assert "frame-ancestors 'none'" in page.headers["Content-Security-Policy"]
        assert client.get("/api/status").status_code == 401
        assert client.post("/api/actions/backup", json={"confirmation": CONFIRM["backup"]}).status_code == 401
        assert start(client, "restore").status_code == 409
        assert client.post("/api/actions/backup", headers=AUTH, json={"confirmation": "no"}).status_code == 400
        for step in ("backup", "verify", "restore", "restart"):
            assert start(client, step).status_code == 202
            state = wait(client)
            assert state["status"] == "ok", state
            assert state["completed"][-1] == step
            assert start(client, step).status_code == 409
        assert state["completed"] == list(web._STEPS)
        assert state["reports"]["restore"]["equal_tables"] == state["reports"]["restore"]["tables"]
        assert state["reports"]["restart"]["memory_tables_equal"] == 4
        assert state["reports"]["restart"]["rank_equal"] is True
        assert "sha256" not in json.dumps(state)
        assert "source_id" not in json.dumps(state)
        assert "target-only" not in json.dumps(state)
        assert client.get("/backup/amora-pre-pcs4c.db").status_code == 404
        assert panel.snapshot.is_file()
        assert (panel.snapshot.stat().st_mode & 0o777) == 0o600
        assert not source_server.streams and not target_server.streams
    # The persistent state is preserved across service restarts.
    reopened = web.RecoveryPanel(panel.root, source, lambda: target)
    assert reopened.status()["completed"] == list(web._STEPS)


def test_interrupted_operation_is_not_retried(remote, tmp_path):
    source, _ = remote
    panel = web.RecoveryPanel(tmp_path / "private", source)
    state = panel._read()
    state.update(status="running", running="restore", completed=["backup", "verify"])
    panel._write(state)
    with TestClient(web.create_app(panel, TOKEN)) as client:
        status = client.get("/api/status", headers=AUTH).json()
        assert status["status"] == "interrupted" and status["next"] is None
        assert start(client, "restore").status_code == 409


def test_same_destination_is_blocked(remote, tmp_path):
    source, _ = remote
    panel = web.RecoveryPanel(tmp_path / "private", source, lambda: source)
    state = panel._read()
    state.update(status="ok", completed=["backup", "verify"])
    panel._write(state)
    with TestClient(web.create_app(panel, TOKEN)) as client:
        assert start(client, "restore").status_code == 409


def test_durable_volume_gate(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_PCS4_WEB_ENABLED", "1")
    monkeypatch.setenv("AMORA_PCS4_WEB_DURABLE", "1")
    monkeypatch.setenv("AMORA_PCS4_WEB_VOLUME", str(tmp_path))
    with pytest.raises(RuntimeError, match="mounted durable"):
        web.create_app()


def test_unauthorized_token_and_parallel_action(remote, tmp_path):
    source, _ = remote
    panel = web.RecoveryPanel(tmp_path / "private", source)
    with pytest.raises(RuntimeError, match="32 bytes"):
        web.create_app(panel, "short")
    fd = panel._lock()
    try:
        with TestClient(web.create_app(panel, TOKEN)) as client:
            assert client.get("/api/status", headers={"Authorization": "Bearer incorrect"}).status_code == 401
            assert start(client, "backup").status_code == 409
    finally:
        import os
        os.close(fd)


@pytest.mark.parametrize("outcome", ["empty", "exact", "mismatch"])
def test_review_interrupted_restore_is_read_only_and_preserves_backup(
    backup, remote, tmp_path, outcome
):
    from amora.storage.pcs4_job import run
    from amora.storage.recovery import restore_empty_remote
    from amora.storage.pcs4_recovery import verify_snapshot

    source, source_server = remote
    target_server = HranaSQLite(tmp_path / "review-target.db")
    target = LibSQLBackend("https://restore.example", "target-only",
                           transport=httpx.MockTransport(target_server))
    panel = web.RecoveryPanel(tmp_path / "review-private", source, lambda: target,
                              online_snapshot=True)
    assert run(panel, "backup")["status"] == "ok"
    assert run(panel, "verify")["status"] == "ok"
    before = verify_snapshot(panel.snapshot)
    state = panel._read()
    reports = json.loads(json.dumps(state["reports"]))
    # Reproduce process death before the final report or state update.
    state.update(status="running", running="restore")
    panel._write(state)
    if outcome in ("exact", "mismatch"):
        restore_empty_remote(target, panel.snapshot)
    if outcome == "mismatch":
        import sqlite3
        with sqlite3.connect(target_server.path) as db:
            db.execute("CREATE TABLE unexpected (id INTEGER)")
    start = len(target_server.calls)
    source_start = len(source_server.calls)
    if outcome == "mismatch":
        with pytest.raises(ValueError, match="PCS4C_SCHEMA_MISMATCH"):
            run(panel, "review_restore")
        assert panel._read() == state
    else:
        result = run(panel, "review_restore")
        assert result["review_outcome"] == outcome
        assert result["next"] == ("restore" if outcome == "empty" else "restart")
        assert result["reports"]["backup"] == reports["backup"]
        assert result["reports"]["verify"] == reports["verify"]
        assert panel._read()["restore_review"]["previous_status"] == "running"
        if outcome == "exact":
            assert result["reports"]["restore"]["equal_tables"] == before["tables"]
    commands = [sql.strip().upper() for sql in target_server.calls[start:]]
    assert all(sql.startswith("SELECT ") or sql in
               {"BEGIN", "ROLLBACK", "PRAGMA FOREIGN_KEYS=ON"} for sql in commands)
    if outcome == "empty":
        assert run(panel, "restore")["completed"] == ["backup", "verify", "restore"]
    assert len(source_server.calls) == source_start
    assert verify_snapshot(panel.snapshot) == before
    assert not source_server.streams and not target_server.streams


def test_review_rejects_unverified_or_wrong_target(remote, tmp_path):
    from amora.storage.pcs4_job import run
    source, _ = remote
    panel = web.RecoveryPanel(tmp_path / "unverified", source, lambda: source)
    with pytest.raises(ValueError, match="PCS4C_REVIEW_STATE"):
        run(panel, "review_restore")
    state = panel._read()
    state.update(status="running", running="restore", completed=["backup", "verify"],
                 reports={"backup": {"status": "ok"}, "verify": {"status": "ok"}})
    panel._write(state)
    with pytest.raises(ValueError, match="PCS4C_DESTINATION_IS_SOURCE"):
        run(panel, "review_restore")
    assert panel._read() == state
