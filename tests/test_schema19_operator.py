"""C.2C.2: existing recovery job, separate v19 state and destination."""
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
from remote_storage_contracts_v13_source import HranaSQLite, remote
from test_pcs4_recovery import backup
from test_pcs4_dev import FakeNorthflank, controller


@pytest.fixture
def operator(backup, remote, tmp_path):
    source, source_server = remote
    former_server = HranaSQLite(tmp_path / "former.db")
    former = LibSQLBackend("https://old-destination.example", "former-token",
                           transport=httpx.MockTransport(former_server))
    target_server = HranaSQLite(tmp_path / "new.db")
    target = LibSQLBackend("https://new-destination.example", "new-token",
                           transport=httpx.MockTransport(target_server))
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
    return panel, target, target_server, former_server, source_server


def action(panel, target, name):
    return run_schema19(panel, name, destination=lambda: target)


def test_full_operator_drill_preserves_old_state_and_both_databases(operator, monkeypatch):
    panel, target, server, old_server, source_server = operator
    previous = panel.state_path.read_bytes()
    old_report = recovery.verify_snapshot(panel.snapshot)
    source_calls = len(source_server.calls)
    with pytest.raises(ValueError, match="STEP_BLOCKED"):
        action(panel, target, "schema19_restore")
    prepared = action(panel, target, "schema19_prepare")
    assert _safe_report(prepared)["next"] == "schema19_restore"
    assert prepared["schema19"]["reports"]["schema19_prepare"]["tables"] == old_report["tables"] + 3
    restored = action(panel, target, "schema19_restore")
    assert _safe_report(restored)["next"] == "schema19_restart"
    boots = []

    def boot(home, timestamp, backend=None):
        with monkeypatch.context() as env:
            env.setenv("AMORA_HOME", str(home))
            env.setenv("AMORA_MODE", "local")
            env.setenv("AMORA_ASSET_STORAGE", "database")
            boots.append(backend is not None)
            return restart._boot(backend or SQLiteBackend(), timestamp)

    monkeypatch.setattr(restart, "_run_boot", boot)
    certified = action(panel, target, "schema19_restart")
    safe = _safe_report(certified)
    assert safe["next"] is None
    assert safe["reports"]["schema19_restart"]["episode_work_tables_equal"] == 3
    assert boots == [False, True, True]
    assert _safe_report(action(panel, target, "schema19_status")) == safe
    assert panel.state_path.read_bytes() == previous
    assert recovery.verify_snapshot(panel.snapshot) == old_report
    assert len(source_server.calls) == source_calls
    assert not old_server.calls and not server.streams


def test_interrupted_restore_review_empty_then_exact(operator):
    panel, target, server, _, _ = operator
    action(panel, target, "schema19_prepare")
    path = panel.root / "schema19" / "state.json"
    state = json.loads(path.read_text())
    state.update(status="running", failed_action="schema19_restore")
    _atomic_json(path, state)
    assert _safe_report(action(panel, target, "schema19_status"))["next"] == "schema19_review_restore"
    reviewed = action(panel, target, "schema19_review_restore")
    assert _safe_report(reviewed)["next"] == "schema19_restore"
    # Simulate committed restore with lost state update.
    candidate = panel.root / "schema19" / "candidate" / "schema19.db"
    recovery.certify_restore(candidate, target, expected_schema=19)
    state = json.loads(path.read_text())
    state.update(status="running", failed_action="schema19_restore", review_outcome=None)
    _atomic_json(path, state)
    exact = action(panel, target, "schema19_review_restore")
    assert _safe_report(exact)["next"] == "schema19_restart"
    assert exact["schema19"]["review_outcome"] == "exact"
    assert not server.streams


def test_wrong_target_incomplete_original_and_changed_pin_are_blocked(operator):
    panel, target, server, _, _ = operator
    with pytest.raises(ValueError, match="DESTINATION_NOT_INDEPENDENT"):
        action(panel, panel.destination(), "schema19_prepare")
    old = panel._read()
    panel._write(dict(old, completed=list(STEPS[:-1])))
    with pytest.raises(ValueError, match="V18_NOT_CERTIFIED"):
        action(panel, target, "schema19_prepare")
    panel._write(old)
    action(panel, target, "schema19_prepare")
    other = LibSQLBackend("https://different.example", "other-token",
                         transport=httpx.MockTransport(server))
    with pytest.raises(ValueError, match="PIN_CHANGED"):
        action(panel, other, "schema19_restore")
    assert not server.calls
    # Restore is refused if the candidate bytes have changed.
    candidate = panel.root / "schema19" / "candidate" / "schema19.db"
    with candidate.open("ab") as file:
        file.write(b"changed")
    failed = action(panel, target, "schema19_restore")
    assert _safe_report(failed)["next"] == "schema19_review_restore"
    assert not server.calls


def test_controller_recovers_v19_error_and_filters_report(operator):
    panel, target, _, _, _ = operator
    prepared = action(panel, target, "schema19_prepare")
    prepared["schema19"]["reports"]["schema19_prepare"]["database_sha256"] = "private"
    assert "private" not in json.dumps(_safe_report(prepared))
    fake = FakeNorthflank()
    fake.runs = [{"id": "run-1", "status": "SUCCESS", "active": 0, "concluded": True,
                  "startedAt": "2026-11-15T01:00:00Z", "concludedAt": "2026-11-15T01:01:00Z"}]
    fake.report = prepared
    control = controller(fake)
    assert control.status()["next"] == "schema19_restore"
    fake.runs[0]["status"] = "FAILED"
    fake.report = {**prepared, "action": "schema19_restore",
                   "schema19": {**prepared["schema19"], "status": "error",
                                "failed_action": "schema19_restore"}}
    assert control.status()["next"] == "schema19_review_restore"
    fake.report["schema19"]["reports"]["schema19_prepare"]["rows"] += 1
    assert control.status()["status"] == "blocked"


def test_old_certified_report_enables_new_prepare_without_rewriting_old_state(operator):
    panel, target, _, _, _ = operator
    original = {"status": "ok", "action": "restart", "running": None,
                "completed": list(STEPS), "reports": panel._read()["reports"]}
    assert _safe_report(original)["next"] == "schema19_prepare"
    assert CONFIRMATIONS["schema19_restore"] == "NOVO DESTINO V19 VAZIO"
