"""Offline, read-only Aiven Phase 1 audit of an existing certified PCS.4C pair.

Not a PostgreSQL import. Does not create/replace snapshots, access Turso/Aiven,
start Core, or reveal paths, hashes, identities, private rows or credentials.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from .pcs4_recovery import _open, verify_snapshot

DEFAULT_SNAPSHOT = Path("/private/pcs4c/backup/amora-pre-pcs4c.db")
DEFAULT_STATE = Path("/private/pcs4c/state.json")
_STEPS = ["backup", "verify", "restore", "restart"]
_COUNTS = ("schema_version", "tables", "rows", "assets")
_FTS5 = re.compile(r"\bUSING\s+fts5\s*\(", re.IGNORECASE)


class SnapshotAuditError(RuntimeError):
    """A fixed, non-sensitive reason code only."""


def audit(snapshot: Path, state_file: Path) -> dict[str, object]:
    snapshot, state_file = Path(snapshot), Path(state_file)
    if not snapshot.is_file() or not state_file.is_file():
        raise SnapshotAuditError("SNAPSHOT_OR_CERTIFICATION_MISSING")
    try:
        original_state = state_file.read_bytes()
        state = json.loads(original_state)
        if (
            not isinstance(state, dict)
            or state.get("status") != "ok"
            or state.get("running") is not None
            or state.get("completed") != _STEPS
            or not isinstance(state.get("reports"), dict)
            or not all(
                isinstance(state["reports"].get(step), dict)
                and state["reports"][step].get("status") == "ok"
                for step in _STEPS
            )
        ):
            raise SnapshotAuditError("PCS4C_CERTIFICATION_INCOMPLETE")

        report = verify_snapshot(snapshot, expected_schema=18)
        if (
            report["database_sha256"] != state.get("database_sha256")
            or report["archive_sha256"] != state.get("archive_sha256")
        ):
            raise SnapshotAuditError("PCS4C_PIN_MISMATCH")
        for step in ("backup", "verify", "restore"):
            saved = state["reports"][step]
            if any(saved.get(k) != report[k] for k in _COUNTS):
                raise SnapshotAuditError("PCS4C_REPORT_MISMATCH")
        with closing(_open(snapshot)) as conn:
            schema = conn.execute(
                "SELECT type,sql FROM sqlite_schema WHERE sql IS NOT NULL"
            ).fetchall()
        counts = {
            "indexes": sum(kind == "index" for kind, _ in schema),
            "triggers": sum(kind == "trigger" for kind, _ in schema),
            "views": sum(kind == "view" for kind, _ in schema),
            "fts5_virtual_tables": sum(
                kind == "table" and bool(_FTS5.search(sql))
                for kind, sql in schema
            ),
        }
        # Fail if the certification record was changed during verification.
        if state_file.read_bytes() != original_state:
            raise SnapshotAuditError("PCS4C_CERTIFICATION_CHANGED")
        return {
            "status": "ok",
            "phase": "aiven-phase1-offline",
            "source": "pcs4c-certified-v18",
            "read_only": True,
            "production_storage_switched": False,
            "postgres_import_performed": False,
            "schema_version": report["schema_version"],
            "tables": report["tables"],
            "rows": report["rows"],
            "assets": report["assets"],
            "snapshot_bytes": report["database_bytes"],
            "archive_bytes": report["archive_bytes"],
            **counts,
            "certification_hashes_match": True,
        }
    except SnapshotAuditError:
        raise
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error, json.JSONDecodeError):
        raise SnapshotAuditError("PCS4C_AUDIT_FAILED") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--snapshot", type=Path, default=DEFAULT_SNAPSHOT)
    parser.add_argument("--state", type=Path, default=DEFAULT_STATE)
    args = parser.parse_args(argv)
    try:
        result = audit(args.snapshot, args.state)
    except SnapshotAuditError as exc:
        result = {"status": "blocked", "reason": str(exc)}
    print("AIVEN_SNAPSHOT_AUDIT " + json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ok" else 2


if __name__ == "__main__":
    raise SystemExit(main())
