"""Narrow Northflank job control for the authenticated Dev operator.

Only a configured dedicated job is addressable. No arbitrary API proxy,
deployment overrides, secret input, raw logs or storage operations in Core.
"""
from __future__ import annotations

import json
import os
import re
import threading
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel

from .pcs4_job import CONFIRMATIONS, MARKER
from .pcs4_web import _STEPS, _REPORT_KEYS

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
_BASE = "https://api.northflank.com/v1"
_NUMERIC = _REPORT_KEYS - {"rank_equal", "hit_counts"}


def _safe_report(raw):
    if not isinstance(raw, dict) or raw.get("status") != "ok":
        raise ValueError("invalid report")
    completed = raw.get("completed")
    if not isinstance(completed, list) or completed != list(_STEPS[:len(completed)]) or not completed:
        raise ValueError("invalid progression")
    if raw.get("action") != completed[-1] or raw.get("running") is not None:
        raise ValueError("invalid action")
    reports = {}
    for step in completed:
        report = raw.get("reports", {}).get(step)
        if not isinstance(report, dict) or report.get("status") != "ok":
            raise ValueError("missing report")
        filtered = {"status": "ok"}
        for key in _NUMERIC:
            value = report.get(key)
            if type(value) is int and 0 <= value <= 10**12:
                filtered[key] = value
        if "rank_equal" in report and type(report["rank_equal"]) is bool:
            filtered["rank_equal"] = report["rank_equal"]
        counts = report.get("hit_counts")
        if isinstance(counts, list) and len(counts) == 5 and all(type(n) is int and 0 <= n <= 50 for n in counts):
            filtered["hit_counts"] = counts
        if step in ("backup", "verify", "restore") and not {"schema_version", "tables", "rows", "assets"} <= filtered.keys():
            raise ValueError("incomplete report")
        if step == "restore" and filtered.get("equal_tables") != filtered.get("tables"):
            raise ValueError("restore mismatch")
        if step == "restart" and (filtered.get("boots") != 2 or filtered.get("query_count") != 5
                                  or filtered.get("memory_tables_equal") != 4
                                  or filtered.get("rank_equal") is not True
                                  or "hit_counts" not in filtered or not any(filtered["hit_counts"])):
            raise ValueError("restart mismatch")
        reports[step] = filtered
    return {"status": "ok", "completed": completed, "reports": reports,
            "next": _STEPS[len(completed)] if len(completed) < len(_STEPS) else None}


def _state(status, *, reason=None, next_step=None):
    return {"status": status, "reason": reason, "completed": [], "reports": {}, "next": next_step}


class NorthflankRecovery:
    def __init__(self, project, job, token, *, team="", transport=None):
        if not all(_ID.fullmatch(x) for x in (project, job)) or (team and not _ID.fullmatch(team)) or not token:
            raise ValueError("invalid recovery job configuration")
        self.path = ("/teams/" + quote(team, safe="") if team else "") + "/projects/" + quote(project, safe="") + "/jobs/" + quote(job, safe="")
        self.token = token
        self.transport = transport
        self.lock = threading.Lock()
        self.pending = None
        self.uncertain = False

    @classmethod
    def from_environment(cls):
        if os.getenv("AMORA_PCS4_DEV_ENABLED") != "1":
            return None
        return cls(os.environ.get("AMORA_PCS4_NF_PROJECT", ""),
                   os.environ.get("AMORA_PCS4_NF_JOB", ""),
                   os.environ.get("AMORA_PCS4_NF_TOKEN", ""),
                   team=os.environ.get("AMORA_PCS4_NF_TEAM", ""))

    def _request(self, method, suffix, **kwargs):
        try:
            with httpx.Client(transport=self.transport, timeout=10, follow_redirects=False,
                              trust_env=False) as client:
                response = client.request(method, _BASE + self.path + suffix,
                    headers={"Authorization": "Bearer " + self.token, "Accept": "application/json"},
                    **kwargs)
                response.raise_for_status()
                if len(response.content) > 512_000:
                    raise ValueError("oversized response")
                return response.json()["data"]
        except Exception:
            raise HTTPException(503, "RECOVERY_JOB_API_UNAVAILABLE") from None

    def _status(self):
        runs = self._request("GET", "/runs", params={"per_page": 20})["runs"]
        if not isinstance(runs, list):
            raise HTTPException(503, "RECOVERY_JOB_STATUS_INVALID")
        if self.pending:
            if not any(run.get("id") == self.pending for run in runs):
                return _state("running")
            self.pending = None
        if self.uncertain:
            return _state("blocked", reason="SUBMISSION_UNCERTAIN_REVIEW_REQUIRED")
        if any(run.get("status") == "RUNNING" or run.get("active") == 1 or not run.get("concluded") for run in runs):
            return _state("running")
        if not runs:
            return _state("idle", next_step="backup")
        latest = max(runs, key=lambda run: run.get("startedAt", ""))
        run_id = latest.get("id", "")
        if not _RUN_ID.fullmatch(run_id):
            raise HTTPException(503, "RECOVERY_JOB_STATUS_INVALID")
        detail = self._request("GET", "/runs/" + quote(run_id, safe=""))
        if detail.get("status") != "SUCCESS" or detail.get("concluded") is not True:
            return _state("error", reason="JOB_FAILED_REVIEW_REQUIRED")
        if not detail.get("startedAt") or not detail.get("concludedAt"):
            return _state("blocked", reason="JOB_REPORT_UNAVAILABLE")
        logs = self._request("GET", "/logs", params={
            "runId": run_id, "type": "runtime", "queryType": "range",
            "startTime": detail["startedAt"], "endTime": detail["concludedAt"],
            "lineLimit": 10, "direction": "backward", "textIncludes": MARKER.strip(),
        })
        found = []
        for entry in logs:
            line = entry.get("log", "")
            if not isinstance(line, str):
                continue
            # Northflank runtime logs include a CRI stdout prefix.
            if line.startswith("stdout F "):
                line = line[len("stdout F "):]
            if line.startswith(MARKER):
                try:
                    found.append(_safe_report(json.loads(line[len(MARKER):])))
                except (ValueError, TypeError, AttributeError):
                    return _state("blocked", reason="JOB_REPORT_INVALID")
        if len(found) != 1:
            return _state("blocked", reason="JOB_REPORT_UNAVAILABLE")
        return found[0]

    def status(self):
        with self.lock:
            return self._status()

    def start(self, action, confirmation):
        if action not in CONFIRMATIONS or confirmation != CONFIRMATIONS[action]:
            raise HTTPException(400, "RECOVERY_CONFIRMATION_REQUIRED")
        with self.lock:
            state = self._status()
            if state["next"] != action:
                raise HTTPException(409, "RECOVERY_STEP_BLOCKED")
            try:
                data = self._request("POST", "/runs", json={
                    "runtimeEnvironment": {"AMORA_PCS4_JOB_ACTION": action},
                })
                run_id = data.get("id", "")
                if not _RUN_ID.fullmatch(run_id):
                    raise ValueError("invalid run id")
                self.pending = run_id
            except Exception:
                # No automatic repeat of an ambiguous run submission.
                self.uncertain = True
                raise HTTPException(503, "RECOVERY_SUBMISSION_UNCERTAIN") from None
            return {"status": "running"}


class RecoveryConfirmation(BaseModel):
    confirmation: str


def register_pcs4_dev_routes(app, controller=None):
    # Disabled/misconfigured recovery must not break the serving Core.
    if controller is None:
        try:
            controller = NorthflankRecovery.from_environment()
        except (ValueError, KeyError):
            controller = None

    def require_operator(request):
        if getattr(request.state, "security_role", None) not in {"operator", "admin"}:
            raise HTTPException(403, "operator required")

    @app.get("/v1/operator/recovery/status")
    def recovery_status(request: Request):
        require_operator(request)
        if controller is None:
            return _state("disabled", reason="RECOVERY_JOB_NOT_CONFIGURED")
        return controller.status()

    @app.post("/v1/operator/recovery/{action}", status_code=202)
    def recovery_start(request: Request, action: str, body: RecoveryConfirmation):
        require_operator(request)
        if controller is None:
            raise HTTPException(503, "RECOVERY_JOB_NOT_CONFIGURED")
        return controller.start(action, body.confirmation)
