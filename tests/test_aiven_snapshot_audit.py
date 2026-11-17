"""No secrets, network or Core bootstrap are involved in the offline audit tests."""
import json
import sqlite3

import pytest

from amora.storage import aiven_snapshot_audit as check


@pytest.fixture
def certified_pair(tmp_path, monkeypatch):
    snapshot = tmp_path / "amora-pre-pcs4c.db"
    state_path = tmp_path / "state.json"
    with sqlite3.connect(snapshot) as db:
        db.execute("CREATE TABLE example(id INTEGER PRIMARY KEY, note TEXT)")
        db.execute("CREATE INDEX idx_example_note ON example(note)")
        db.execute("INSERT INTO example(note) VALUES ('synthetic')")
    report = {
        "schema_version": 18, "tables": 51, "rows": 48059, "assets": 0,
        "database_sha256": "synthetic-pin-1", "archive_sha256": "synthetic-pin-2",
        "database_bytes": snapshot.stat().st_size, "archive_bytes": 42,
    }
    monkeypatch.setattr(check, "verify_snapshot",
                        lambda path, *, expected_schema: report.copy())
    metadata = {k: report[k] for k in check._COUNTS}
    state = {
        "status": "ok", "running": None, "completed": check._STEPS,
        "database_sha256": report["database_sha256"],
        "archive_sha256": report["archive_sha256"],
        "reports": {
            action: {"status": "ok", **metadata}
            for action in check._STEPS
        },
    }
    state_path.write_text(json.dumps(state))
    return snapshot, state_path, state


def test_certified_audit_only_exposes_aggregate_counts(certified_pair, capsys):
    snapshot, state_path, _ = certified_pair
    before = (snapshot.read_bytes(), state_path.read_bytes())
    assert check.main(["--snapshot", str(snapshot), "--state", str(state_path)]) == 0
    output = capsys.readouterr().out
    assert output.startswith("AIVEN_SNAPSHOT_AUDIT ")
    payload = json.loads(output.split(" ", 1)[1])
    assert payload["status"] == "ok"
    assert payload["certification_hashes_match"] is True
    assert payload["schema_version"] == 18
    assert payload["tables"] == 51
    assert payload["indexes"] >= 1
    assert payload["production_storage_switched"] is False
    assert payload["postgres_import_performed"] is False
    assert "synthetic-pin" not in output
    assert "amora-pre-pcs4c.db" not in output
    assert "synthetic" not in output
    assert (snapshot.read_bytes(), state_path.read_bytes()) == before


@pytest.mark.parametrize("mutation,reason", [
    ("pin", "PCS4C_PIN_MISMATCH"),
    ("report", "PCS4C_REPORT_MISMATCH"),
    ("incomplete", "PCS4C_CERTIFICATION_INCOMPLETE"),
])
def test_fails_closed_on_modified_certification(certified_pair, mutation, reason, capsys):
    snapshot, state_path, state = certified_pair
    if mutation == "pin":
        state["database_sha256"] = "different"
    elif mutation == "report":
        state["reports"]["verify"]["rows"] = 1
    else:
        state["completed"] = ["backup"]
    state_path.write_text(json.dumps(state))
    assert check.main(["--snapshot", str(snapshot), "--state", str(state_path)]) == 2
    payload = json.loads(capsys.readouterr().out.split(" ", 1)[1])
    assert payload == {"status": "blocked", "reason": reason}


def test_missing_files_fail_closed(tmp_path, capsys):
    assert check.main(["--snapshot", str(tmp_path / "private.db"),
                       "--state", str(tmp_path / "state.json")]) == 2
    assert "SNAPSHOT_OR_CERTIFICATION_MISSING" in capsys.readouterr().out


def test_dependency_exception_never_leaks_private_content(certified_pair, monkeypatch, capsys):
    snapshot, state_path, _ = certified_pair
    def fail(*args, **kwargs):
        raise RuntimeError("secret URI and memory text")
    monkeypatch.setattr(check, "verify_snapshot", fail)
    assert check.main(["--snapshot", str(snapshot), "--state", str(state_path)]) == 2
    output = capsys.readouterr().out
    assert json.loads(output.split(" ", 1)[1])["reason"] == "PCS4C_AUDIT_FAILED"
    assert "secret" not in output and "memory text" not in output
