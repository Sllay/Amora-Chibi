"""Narrow Northflank job control for the authenticated Dev operator.

Only a configured dedicated job is addressable. No arbitrary API proxy,
deployment overrides, secret input, raw logs or storage operations in Core.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import json
import os
import re
import threading
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request
from pydantic import BaseModel

from .pcs4_contract import (
    CONFIRMATIONS, MARKER, STEPS as _STEPS, REPORT_KEYS as _REPORT_KEYS,
    SCHEMA19_STEPS, SCHEMA19_ACTIONS, SCHEMA19_REPORT_KEYS, SCHEMA19_ERROR_CODES,
)

_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
_RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9-]{0,127}$")
_BASE = "https://api.northflank.com/v1"
_NUMERIC = _REPORT_KEYS - {"rank_equal", "hit_counts"}
# The dashboard may show a CRI timestamp in front of the stream prefix;
# the documented API example instead supplies ts as a separate field.
_CRI_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,9})?(?:Z|[+-]\d{2}:\d{2}) ")


def _report_line(line):
    if not isinstance(line, str):
        return None
    line = _CRI_TIMESTAMP.sub("", line, count=1)
    if line.startswith("stdout F "):
        line = line[len("stdout F "):]
    # Never accept partial CRI frames, stderr, or a marker embedded in
    # unrelated log text.
    return line[len(MARKER):] if line.startswith(MARKER) else None


def _report_window(started, concluded):
    start = datetime.fromisoformat(started.replace("Z", "+00:00"))
    end = datetime.fromisoformat(concluded.replace("Z", "+00:00"))
    if start.tzinfo is None or end.tzinfo is None or end < start:
        raise ValueError("invalid run time")
    # Provider run boundaries and container log timestamps can differ.
    # The runId still strictly scopes every query to the same execution.
    return ((start - timedelta(minutes=2)).astimezone(timezone.utc).isoformat(),
            (end + timedelta(minutes=2)).astimezone(timezone.utc).isoformat())



def _safe_report(raw):
    if isinstance(raw, dict) and raw.get("action") in SCHEMA19_ACTIONS:
        if raw.get("status") == "error":
            if raw.get("reason") not in SCHEMA19_ERROR_CODES:
                raise ValueError("invalid v19 failure")
            return _state("error", reason=raw["reason"], next_step="schema19_status")
        return _safe_schema19(raw)
    if not isinstance(raw, dict) or raw.get("status") != "ok":
        raise ValueError("invalid report")
    completed = raw.get("completed")
    if not isinstance(completed, list) or completed != list(_STEPS[:len(completed)]) or not completed:
        raise ValueError("invalid progression")
    action = raw.get("action")
    outcome = raw.get("review_outcome")
    if action == "review_restore":
        if (outcome, completed) not in (("empty", ["backup", "verify"]),
                                         ("exact", ["backup", "verify", "restore"])):
            raise ValueError("invalid review outcome")
    elif action != completed[-1] or outcome is not None:
        raise ValueError("invalid action")
    if raw.get("running") is not None:
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
    state = {"status": "ok", "completed": completed, "reports": reports,
             "next": _STEPS[len(completed)] if len(completed) < len(_STEPS) else None}
    if action == "review_restore":
        state["review_outcome"] = outcome
    if completed == list(_STEPS):
        state["next"] = "schema19_prepare"
    return state



def _safe_schema19(raw):
    from .schema19_diagnostic import safe_diagnostic
    if raw.get("status") != "ok" or raw.get("completed") != list(_STEPS):
        raise ValueError("invalid v18 prefix")
    base = _safe_report({**raw, "action": "restart"})
    original = base["reports"]["backup"]
    if (original["schema_version"] != 18 or any(
        base["reports"][step].get(key) != original[key]
        for step in ("verify", "restore") for key in ("schema_version", "tables", "rows", "assets")
    ) or base["reports"]["restart"].get("schema_version") != 18):
        raise ValueError("invalid v18 evidence")
    drill = raw.get("schema19")
    if not isinstance(drill, dict):
        raise ValueError("invalid v19 report")
    completed = drill.get("completed")
    status = drill.get("status")
    failed = drill.get("failed_action")
    outcome = drill.get("review_outcome")
    reason = drill.get("reason")
    if reason is not None and (status != "error" or reason not in SCHEMA19_ERROR_CODES):
        raise ValueError("invalid v19 reason")
    if (not isinstance(completed, list) or completed != list(SCHEMA19_STEPS[:len(completed)])
            or status not in {"idle", "ok", "error"}
            or (status == "idle" and completed)
            or (status == "error" and failed not in SCHEMA19_ACTIONS)
            or (status != "error" and failed is not None)):
        raise ValueError("invalid v19 progression")
    action = raw["action"]
    if action not in {"schema19_status", "schema19_review_migration"}:
        if status == "ok" and (not completed or action != completed[-1]):
            raise ValueError("invalid v19 action")
    if outcome not in {None, "v18_exact", "exact"}:
        raise ValueError("invalid v19 review")
    if outcome == "v18_exact" and completed != ["schema19_prepare"]:
        raise ValueError("invalid v19 review")
    if outcome == "exact" and completed != ["schema19_prepare", "schema19_migrate"]:
        raise ValueError("invalid v19 review")
    reports = {}
    baseline_rows = original["rows"]
    historical = drill.get("historical_diagnostic")
    for step in completed:
        item = drill.get("reports", {}).get(step)
        if not isinstance(item, dict) or item.get("status") != "ok":
            raise ValueError("missing v19 evidence")
        safe = {"status": "ok"}
        for key in SCHEMA19_REPORT_KEYS - {"rank_equal", "hit_counts", "migration_history_preserved"}:
            value = item.get(key)
            if type(value) is int and 0 <= value <= 10**12:
                safe[key] = value
        for key in ("rank_equal", "migration_history_preserved"):
            if type(item.get(key)) is bool:
                safe[key] = item[key]
        counts = item.get("hit_counts")
        if isinstance(counts, list) and len(counts) == 5 and all(type(n) is int and 0 <= n <= 50 for n in counts):
            safe["hit_counts"] = counts
        if safe.get("schema_version") != 19 or safe.get("tables") != original["tables"] + 3:
            raise ValueError("v19 schema mismatch")
        if step == "schema19_prepare" and ("baseline_rows" in item or historical is not None):
            if (safe.get("historical_rows") != original["rows"]
                    or safe.get("historical_memory_tables_equal") != 7
                    or "baseline_rows" not in safe or historical is None):
                raise ValueError("v19 baseline evidence mismatch")
            baseline_rows = safe["baseline_rows"]
        if step != "schema19_restart" and (safe.get("rows") != baseline_rows + 1 or safe.get("assets") != original["assets"]):
            raise ValueError("v19 rows mismatch")
        if step == "schema19_prepare" and (
            safe.get("source_schema_version") != 18 or safe.get("old_tables") != original["tables"]
            or safe.get("preserved_tables") != original["tables"] - 1
            or safe.get("new_tables") != 3 or safe.get("migration_history_preserved") is not True
            or "relationship_sequence_delta" not in safe
        ):
            raise ValueError("v19 upgrade mismatch")
        if step != "schema19_prepare" and safe.get("equal_tables") != safe["tables"]:
            raise ValueError("v19 restore mismatch")
        if step == "schema19_restart" and (
            safe.get("boots") != 2 or safe.get("query_count") != 5
            or safe.get("memory_tables_equal") != 4 or safe.get("episode_work_tables_equal") != 3
            or safe.get("rank_equal") is not True or not any(safe.get("hit_counts", []))
        ):
            raise ValueError("v19 restart mismatch")
        reports[step] = safe
    if historical is not None:
        from .schema19_migration import HISTORICAL_MEMORY_TABLES
        if "schema19_prepare" not in reports:
            raise ValueError("unexpected baseline evidence")
        checked = safe_diagnostic(historical)
        if (any(checked[k] != 0 for k in ("schema_missing_objects", "schema_added_objects", "schema_changed_objects"))
                or any(x["status"] != "rows" or x["schema_changed"]
                       or x["table"] in HISTORICAL_MEMORY_TABLES | {"schema_migrations", "platform_identity_bindings"}
                       for x in checked["differences"])):
            raise ValueError("v19 historical critical drift")
        if checked["unknown_tables"] == 0 and sum(
            x["destination_rows"] - x["backup_rows"] for x in checked["differences"]
        ) != baseline_rows - original["rows"]:
            raise ValueError("v19 baseline count mismatch")
        reports["schema19_historical_diagnostic"] = checked
    diagnostic = drill.get("diagnostic")
    if diagnostic is not None:
        if status != "error" or reason != "SCHEMA19_DESTINATION_DIVERGED":
            raise ValueError("unexpected v19 diagnostic")
        reports["schema19_diagnostic"] = safe_diagnostic(diagnostic)
    next_step = SCHEMA19_STEPS[len(completed)] if len(completed) < len(SCHEMA19_STEPS) else None
    if status == "error":
        if failed == "schema19_prepare" and not completed:
            next_step = "schema19_prepare"
        elif failed in {"schema19_migrate", "schema19_review_migration"} and completed == ["schema19_prepare"]:
            next_step = "schema19_review_migration"
        else:
            next_step = None
    return {"status": "error" if status == "error" else "ok",
            "reason": (reason or "SCHEMA19_REVIEW_REQUIRED") if status == "error" else None,
            "completed": list(_STEPS), "reports": {**base["reports"], **reports},
            "next": next_step, "schema19": {"status": status, "completed": completed},
            "review_outcome": outcome}

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
        # This legacy recovery controller is *Northflank-only*. Never let a
        # stale exported token or enabled flag trigger a recovery job against
        # the old provider once the Core is hosted on Altare. Its endpoints
        # will remain disabled until a separately certified Altare adapter
        # exists (jobs and log contracts differ).
        from amora.deployment_profiles import get_profile
        if os.getenv("ALTARE") == "1" or get_profile().host != "northflank":
            return None
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
        if detail.get("concluded") is not True:
            return _state("error", reason="JOB_FAILED_REVIEW_REQUIRED", next_step="review_restore")
        failed_run = detail.get("status") != "SUCCESS"
        if not detail.get("startedAt") or not detail.get("concludedAt"):
            return _state("blocked", reason="JOB_REPORT_UNAVAILABLE")
        try:
            start, end = _report_window(detail["startedAt"], detail["concludedAt"])
        except (ValueError, TypeError, AttributeError, OverflowError):
            return _state("blocked", reason="JOB_REPORT_UNAVAILABLE")
        # Read a bounded tail of this exact run without relying on the
        # provider's text-search index. The report is the worker's final line.
        params = {
            "runId": run_id, "type": "runtime", "queryType": "range",
            "startTime": start, "endTime": end,
            "lineLimit": 100, "direction": "backward",
        }
        logs = self._request("GET", "/logs", params=params)
        if not isinstance(logs, list):
            return _state("blocked", reason="JOB_REPORT_INVALID")
        found = []
        for entry in logs:
            if not isinstance(entry, dict):
                return _state("blocked", reason="JOB_REPORT_INVALID")
            payload = _report_line(entry.get("log"))
            if payload is None:
                continue
            try:
                raw = json.loads(payload)
                if failed_run and (not isinstance(raw, dict) or raw.get("action") not in SCHEMA19_ACTIONS):
                    continue
                report = _safe_report(raw)
                if failed_run and report["status"] != "error":
                    continue
            except (ValueError, TypeError, AttributeError):
                return _state("blocked", reason="JOB_REPORT_INVALID")
            if report not in found:
                found.append(report)
        if not found:
            return (_state("error", reason="JOB_FAILED_REVIEW_REQUIRED", next_step="review_restore")
                    if failed_run else _state("blocked", reason="JOB_REPORT_UNAVAILABLE"))
        if len(found) != 1:
            return _state("blocked", reason="JOB_REPORT_INVALID")
        return found[0]

    def status(self):
        with self.lock:
            return self._status()

    def start(self, action, confirmation):
        if action not in CONFIRMATIONS or confirmation != CONFIRMATIONS[action]:
            raise HTTPException(400, "RECOVERY_CONFIRMATION_REQUIRED")
        with self.lock:
            state = self._status()
            if state["next"] != action and not (
                action == "schema19_status" and (state["status"] in {"error", "blocked"}
                    or (state["status"] == "ok" and state["completed"] == list(_STEPS)))
                and not self.uncertain
            ):
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
