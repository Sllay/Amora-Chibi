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
        self.logs = None
        self.expected_window = ("2026-11-15T00:58:00+00:00", "2026-11-15T01:03:00+00:00")
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
            assert "textIncludes" not in request.url.params
            assert request.url.params["lineLimit"] == "100"
            assert (request.url.params["startTime"], request.url.params["endTime"]) == self.expected_window
            rows = self.logs if self.logs is not None else ([{"log": "stdout F " + MARKER + json.dumps(self.report)}] if self.report else [])
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


def test_real_northflank_log_format_recovers_existing_backup():
    """Reproduce the observed CRI line, including nanosecond timestamp."""
    fake = FakeNorthflank()
    # Simulate provider timestamps rounded down to seconds at the run boundary.
    fake.runs = [{"id": "run-1", "status": "SUCCESS", "active": 0, "concluded": True,
                  "startedAt": "2026-11-15T14:32:51Z", "concludedAt": "2026-11-15T14:33:23Z"}]
    fake.expected_window = ("2026-11-15T14:30:51+00:00", "2026-11-15T14:35:23+00:00")
    report = {"status": "ok", "running": None, "completed": ["backup"],
              "reports": {"backup": {"status": "ok", "rows": 48059, "schema_version": 18,
                                     "assets": 0, "tables": 51}}, "reason": None,
              "next": "verify", "action": "backup"}
    line = "2026-11-15T14:33:23.637046321Z stdout F " + MARKER + json.dumps(report)
    fake.logs = [
        {"log": "2026-11-15T14:33:24.018846197Z stdout F Process terminated with exit code 0"},
        {"log": line},
        {"log": "stdout F " + MARKER + json.dumps(report)},  # duplicate shipping, same report
        {"log": "stdout F unrelated private text"},
    ]
    control = controller(fake)
    result = control.status()
    assert result["status"] == "ok" and result["next"] == "verify"
    assert result["reports"]["backup"]["rows"] == 48059
    assert "private text" not in json.dumps(result)
    assert all(method == "GET" for method, _ in fake.calls)
    # A second distinct report is never accepted as certification.
    report["reports"]["backup"]["rows"] = 1
    fake.logs.append({"log": "stdout F " + MARKER + json.dumps(report)})
    assert control.status()["reason"] == "JOB_REPORT_INVALID"


def test_missing_report_stays_blocked_and_partial_frames_not_accepted():
    fake = FakeNorthflank()
    fake.runs = [{"id": "run-1", "status": "SUCCESS", "active": 0, "concluded": True,
                  "startedAt": "2026-11-15T01:00:00Z", "concludedAt": "2026-11-15T01:01:00Z"}]
    fake.logs = [
        {"log": "stdout P " + MARKER + "{}"},
        {"log": "stderr F " + MARKER + "{}"},
        {"log": "stdout F unrelated " + MARKER + "{}"},
    ]
    control = controller(fake)
    assert control.status()["reason"] == "JOB_REPORT_UNAVAILABLE"
    from fastapi import HTTPException
    with pytest.raises(HTTPException):
        control.start("verify", CONFIRMATIONS["verify"])
    assert all(method == "GET" for method, _ in fake.calls)
