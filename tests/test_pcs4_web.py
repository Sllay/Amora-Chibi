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
