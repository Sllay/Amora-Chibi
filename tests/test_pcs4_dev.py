"""PCS.4C Dev integration: scoped API, job protocol, read-only backup."""
import json

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from amora.storage.pcs4_dev import NorthflankRecovery, register_pcs4_dev_routes
from amora.storage.pcs4_contract import CONFIRMATIONS, MARKER
from amora.storage.pcs4_job import run
from amora.storage.pcs4_web import RecoveryPanel
from remote_storage_contracts_v13_source import remote
from test_pcs4_recovery import backup


class FakeNorthflank:
    def __init__(self):
        self.runs = []
        self.calls = []
        self.report = None
        self.fail_submission = False

    def __call__(self, request):
        assert request.url.host == "api.northflank.com"
        assert request.headers["Authorization"] == "Bearer job-control-secret"
        assert request.url.path.startswith("/v1/projects/project/jobs/recovery/")
        self.calls.append((request.method, request.url.path))
        path = request.url.path
        if path.endswith("/runs") and request.method == "POST":
            assert json.loads(request.content) == {"runtimeEnvironment": {"AMORA_PCS4_JOB_ACTION": "backup"}}
            if self.fail_submission:
                raise httpx.ReadTimeout("secret")
            self.runs.append({"id": "run-1", "status": "RUNNING", "active": 1, "concluded": False,
                              "startedAt": "2026-11-15T01:00:00Z"})
            return httpx.Response(200, json={"data": {"id": "run-1"}})
        if path.endswith("/runs"):
            return httpx.Response(200, json={"data": {"runs": self.runs}})
        if path.endswith("/runs/run-1"):
            return httpx.Response(200, json={"data": self.runs[0]})
        if path.endswith("/logs"):
            assert request.url.params["runId"] == "run-1"
            assert request.url.params["textIncludes"] == MARKER.strip()
            rows = [{"log": "stdout F " + MARKER + json.dumps(self.report)}] if self.report else []
            return httpx.Response(200, json={"data": rows})
        return httpx.Response(404)


def controller(fake):
    return NorthflankRecovery("project", "recovery", "job-control-secret",
                             transport=httpx.MockTransport(fake))


def test_job_controller_order_and_sanitized_report():
    fake = FakeNorthflank()
    control = controller(fake)
    assert control.status()["next"] == "backup"
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        control.start("restore", CONFIRMATIONS["restore"])
    assert e.value.status_code == 409
    assert control.start("backup", CONFIRMATIONS["backup"]) == {"status": "running"}
    assert control.status()["status"] == "running"
    with pytest.raises(HTTPException):
        control.start("backup", CONFIRMATIONS["backup"])
    fake.runs[0].update(status="SUCCESS", active=0, concluded=True,
                        concludedAt="2026-11-15T01:01:00Z")
    assert control.status()["status"] == "blocked"  # no success without report
    fake.report = {"status": "ok", "action": "backup", "completed": ["backup"], "running": None,
                   "reports": {"backup": {"status": "ok", "schema_version": 18, "tables": 50,
                                          "rows": 274, "assets": 3, "database_sha256": "secret",
                                          "extra": "private text"}}}
    state = control.status()
    assert state["next"] == "verify"
    assert "secret" not in json.dumps(state) and "private text" not in json.dumps(state)
    fake.report["reports"]["backup"]["tables"] = "not a count"
    assert control.status()["status"] == "blocked"


def test_ambiguous_submission_never_retried():
    fake = FakeNorthflank()
    fake.fail_submission = True
    control = controller(fake)
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as e:
        control.start("backup", CONFIRMATIONS["backup"])
    assert e.value.status_code == 503
    assert control.status()["status"] == "blocked"
    with pytest.raises(HTTPException):
        control.start("backup", CONFIRMATIONS["backup"])
    assert sum(m == "POST" for m, _ in fake.calls) == 1


def test_route_requires_operator_role():
    fake = FakeNorthflank()
    app = FastAPI()
    @app.middleware("http")
    async def roles(request: Request, call_next):
        request.state.security_role = request.headers.get("x-test-role")
        return await call_next(request)
    register_pcs4_dev_routes(app, controller(fake))
    with TestClient(app) as client:
        for role in ("web", "avatar", "messaging", "service"):
            assert client.get("/v1/operator/recovery/status", headers={"x-test-role": role}).status_code == 403
            assert client.post("/v1/operator/recovery/backup", headers={"x-test-role": role},
                               json={"confirmation": CONFIRMATIONS["backup"]}).status_code == 403
        assert client.get("/v1/operator/recovery/status", headers={"x-test-role": "operator"}).json()["next"] == "backup"
        assert client.post("/v1/operator/recovery/backup", headers={"x-test-role": "operator"},
                           json={"confirmation": CONFIRMATIONS["backup"]}).status_code == 202


def test_job_online_backup_reuses_audited_export_without_writer_reservation(backup, remote, tmp_path):
    source, server = remote
    panel = RecoveryPanel(tmp_path / "private", source, online_snapshot=True)
    begin = len(server.calls)
    result = run(panel, "backup")
    assert result["status"] == "ok", result
    assert result["completed"] == ["backup"]
    commands = [sql.strip().upper() for sql in server.calls[begin:]]
    assert "BEGIN" in commands and "ROLLBACK" in commands
    assert "BEGIN IMMEDIATE" not in commands
    # Hrana initializes each stream with a connection-local foreign-key setting.
    # This does not write database contents or reserve a writer.
    allowed = {"BEGIN", "ROLLBACK", "PRAGMA FOREIGN_KEYS=ON"}
    unexpected = [sql for sql in commands if not sql.startswith("SELECT ") and sql not in allowed]
    assert not unexpected, unexpected
    assert run(panel, "verify")["completed"] == ["backup", "verify"]
    assert not server.streams
