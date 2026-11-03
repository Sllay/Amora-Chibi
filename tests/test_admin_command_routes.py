from __future__ import annotations

import time
from pathlib import Path

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.operator_auth import issue_operator_token
from amora.security import issue_browser_token
from amora.time import SimulatedClock


ADMIN = "test-admin-command-api-token-32-bytes-minimum"
MASTER = "test-admin-command-dev-master-32-bytes-minimum"


def _core(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_MODE", "cloud")
    monkeypatch.setenv("AMORA_PUBLIC_ORIGINS", "https://amora.example")
    monkeypatch.setenv("AMORA_DURABLE_FILESYSTEM", "true")
    monkeypatch.setenv("AMORA_SECRET_LOCAL_API_TOKEN", ADMIN)
    monkeypatch.setenv("AMORA_DEV_MASTER_KEY", MASTER)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    client = TestClient(create_api(core), base_url="https://amora.example")
    return core, client


def _terminal_job(client: TestClient, headers: dict[str, str], job_id: str) -> dict[str, object]:
    for _ in range(40):
        response = client.get(f"/v1/operator/commands/{job_id}", headers=headers)
        assert response.status_code == 200, response.text
        job = response.json()
        if job["status"] in {"succeeded", "failed", "timed_out"}:
            return job
        time.sleep(0.01)
    raise AssertionError("admin command did not reach a terminal state")


def test_admin_commands_are_operator_scoped_and_status_runs_through_api(tmp_path: Path, monkeypatch) -> None:
    core, client = _core(tmp_path, monkeypatch)
    try:
        web = {"Authorization": "Bearer " + issue_browser_token(MASTER, core.clock, "web")}
        operator = {"Authorization": "Bearer " + issue_operator_token(MASTER, core.clock)}

        assert client.get("/v1/operator/commands", headers=web).status_code == 403
        assert client.get("/v1/operator/commands/catalog", headers=operator).status_code == 200

        started = client.post(
            "/v1/operator/commands",
            headers=operator,
            json={"command": "status"},
        )
        assert started.status_code == 202, started.text
        queued = started.json()
        assert queued["command_path"] == "status"
        assert queued["permission"] == "operator"

        finished = _terminal_job(client, operator, queued["job_id"])
        assert finished["status"] == "succeeded"
        assert finished["result"]["kind"] == "core_status"
        assert finished["result"]["readiness"]["status"] in {"ready", "degraded", "not_ready"}

        history = client.get("/v1/operator/commands", headers=operator)
        assert history.status_code == 200
        assert any(item["job_id"] == queued["job_id"] for item in history.json())
    finally:
        client.close()
        core.close()


def test_admin_command_api_rejects_arbitrary_host_shell_text(tmp_path: Path, monkeypatch) -> None:
    core, client = _core(tmp_path, monkeypatch)
    try:
        operator = {"Authorization": "Bearer " + issue_operator_token(MASTER, core.clock)}
        response = client.post(
            "/v1/operator/commands",
            headers=operator,
            json={"command": "rm -rf /"},
        )
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == "COMMAND_NOT_FOUND"
    finally:
        client.close()
        core.close()
