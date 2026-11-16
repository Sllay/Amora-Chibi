"""Operator-only v19 drill on the existing PCS.4C job and private volume.

Never updates the v18 state, backup or serving schema.
A separate private state pins the previously certified test destination.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile

from .pcs4_recovery import (
    _origin_host, verify_snapshot,
)
from .pcs4_restart import certify_restart
from .pcs4_web import _atomic_json, _origin_digest, _sync_dir
from .schema19_migration import migrate_certified_destination, review_certified_destination
from .schema19_drill import prepare_schema19_copy, compare_upgrade
from .pcs4_contract import STEPS, SCHEMA19_STEPS, SCHEMA19_ACTIONS, SCHEMA19_REPORT_KEYS



def _public_report(report):
    return {"status": "ok", **{key: report[key] for key in SCHEMA19_REPORT_KEYS if key in report}}


def _state_report(old, state, action):
    # Old reports are already filtered by RecoveryPanel. Never export hashes,
    # host identifiers, paths, exception text or credentials.
    return {"status": "ok", "action": action, "running": None,
            "completed": list(STEPS), "reports": old["reports"],
            "schema19": {
                "status": state["status"], "completed": state["completed"],
                "reports": state["reports"], "failed_action": state.get("failed_action"),
                "review_outcome": state.get("review_outcome"),
            }}


def run_schema19(panel, action):
    if action not in SCHEMA19_ACTIONS:
        raise ValueError("SCHEMA19_ACTION_INVALID")
    fd = panel._lock()
    try:
        old = panel._read()
        if (old.get("source_id") != panel.source_id or old.get("status") != "ok"
                or old.get("running") is not None or old.get("completed") != list(STEPS)
                or not old.get("destination_id")
                or any(old.get("reports", {}).get(step, {}).get("status") != "ok" for step in STEPS)):
            raise ValueError("SCHEMA19_V18_NOT_CERTIFIED")
        original = verify_snapshot(panel.snapshot)
        panel._pin(old, original)
        for step in ("backup", "verify", "restore"):
            if any(old["reports"][step].get(key) != original[key]
                   for key in ("schema_version", "tables", "rows", "assets")):
                raise ValueError("SCHEMA19_V18_NOT_CERTIFIED")
        restart_report = old["reports"]["restart"]
        if (old["reports"]["restore"].get("equal_tables") != original["tables"]
                or restart_report.get("schema_version") != 18
                or restart_report.get("equal_tables") != original["tables"]
                or restart_report.get("boots") != 2 or restart_report.get("query_count") != 5
                or restart_report.get("rank_equal") is not True
                or restart_report.get("memory_tables_equal") != 4):
            raise ValueError("SCHEMA19_V18_NOT_CERTIFIED")
        # Reuse only the certified v18 test database; production is forbidden.
        target = panel.destination()
        if old["destination_id"] != _origin_digest(target):
            raise ValueError("SCHEMA19_V18_DESTINATION_CHANGED")
        if (_origin_host(panel.source.url) == _origin_host(target.url)
                or target._token == panel.source._token):
            raise ValueError("SCHEMA19_DESTINATION_NOT_INDEPENDENT")

        root = panel.root / "schema19"
        root.mkdir(mode=0o700, exist_ok=True)
        state_path = root / "state.json"
        pair = root / "candidate"
        snapshot = pair / "schema19.db"
        state = (json.loads(state_path.read_text()) if state_path.exists() else {
            "status": "idle", "completed": [], "reports": {},
            "source_sha": original["database_sha256"], "source_archive_sha": original["archive_sha256"],
            "destination_id": _origin_digest(target),
        })
        if (state["source_sha"] != original["database_sha256"]
                or state["source_archive_sha"] != original["archive_sha256"]
                or state["destination_id"] != _origin_digest(target)):
            raise ValueError("SCHEMA19_PIN_CHANGED")
        if state["status"] == "running":
            state.update(status="error")
            _atomic_json(state_path, state)

        def pinned():
            report = verify_snapshot(snapshot, expected_schema=19)
            if (state.get("database_sha256") != report["database_sha256"]
                    or state.get("archive_sha256") != report["archive_sha256"]):
                raise ValueError("SCHEMA19_CANDIDATE_CHANGED")
            return report

        if action == "schema19_status":
            if state["completed"]:
                pinned()
            return _state_report(old, state, action)
        completed = state["completed"]
        expected = SCHEMA19_STEPS[len(completed)] if len(completed) < len(SCHEMA19_STEPS) else None
        failed = state.get("failed_action")
        allowed = (
            (state["status"] in {"idle", "ok"} and action == expected)
            or (state["status"] == "error" and failed == "schema19_prepare"
                and not completed and action == "schema19_prepare")
            or (state["status"] == "error" and failed in {"schema19_migrate", "schema19_review_migration"}
                and completed == ["schema19_prepare"] and action == "schema19_review_migration")
        )
        if not allowed:
            raise ValueError("SCHEMA19_STEP_BLOCKED")
        state.update(status="running", failed_action=action, review_outcome=None)
        _atomic_json(state_path, state)
        try:
            if action == "schema19_prepare":
                # If interrupted after publish but before state write, adopt
                # only a verified exact upgrade of the original pinned backup.
                if pair.exists():
                    report = verify_snapshot(snapshot, expected_schema=19)
                    report.update(compare_upgrade(panel.snapshot, snapshot))
                else:
                    with tempfile.TemporaryDirectory(prefix="staging-", dir=root) as staging:
                        prepared, report = prepare_schema19_copy(panel.snapshot, Path(staging) / "pair")
                        for path in (prepared, Path(str(prepared) + ".avatar.zip")):
                            with path.open("rb") as stream:
                                os.fsync(stream.fileno())
                        _sync_dir(prepared.parent)
                        prepared.parent.rename(pair)
                        _sync_dir(root)
                if report["archive_sha256"] != original["archive_sha256"]:
                    raise ValueError("SCHEMA19_SOURCE_CHANGED")
                state["database_sha256"] = report["database_sha256"]
                state["archive_sha256"] = report["archive_sha256"]
                report["source_schema_version"] = 18
            elif action == "schema19_migrate":
                pinned()
                report = migrate_certified_destination(panel.snapshot, snapshot, target)
                pinned()
            elif action == "schema19_review_migration":
                pinned()
                outcome, report = review_certified_destination(panel.snapshot, snapshot, target)
                pinned()
                if outcome not in {"v18_exact", "exact"}:
                    raise ValueError("SCHEMA19_REVIEW_INVALID")
            else:
                pinned()
                report = certify_restart(snapshot, target, expected_schema=19)
                pinned()
            panel._pin(old, verify_snapshot(panel.snapshot))
            if action == "schema19_review_migration":
                state["review_outcome"] = outcome
                if outcome == "exact":
                    state["reports"]["schema19_migrate"] = _public_report(report)
                    completed.append("schema19_migrate")
            else:
                state["reports"][action] = _public_report(report)
                completed.append(action)
            state.update(status="ok", failed_action=None)
        except Exception:
            # Keep the previous certified prefix; never implicitly rerun a
            # potentially committed migration or restart.
            state.update(status="error")
        _atomic_json(state_path, state)
        return _state_report(old, state, action)
    finally:
        os.close(fd)
