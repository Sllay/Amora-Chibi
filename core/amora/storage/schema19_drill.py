"""Offline schema-19 engineering drill; never upgrades a live Core database.

The input is an already verified, quiescent v18 backup pair. The output is a
new private directory containing a v19 copy. Runtime schema remains 18.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import shutil
import sqlite3

from .pcs4_recovery import _open, _row_hashes, _schema, _sha, _tables, verify_snapshot
from .remote import statements
from .sensory_episode_schema import SENSORY_EPISODE_QUEUE_CANDIDATE_SQL

_CANDIDATE_TABLES = frozenset({
    "sensory_episode_jobs", "sensory_episode_job_evidence", "sensory_episode_invalidations",
})
_CANDIDATE_INDEXES = frozenset({
    "idx_sensory_episode_due", "idx_sensory_episode_session_status", "idx_sensory_episode_evidence",
})


def compare_upgrade(source: Path, upgraded: Path) -> dict:
    """Require exact old schema/rows and exactly the expected additive objects."""
    with closing(_open(source)) as before, closing(_open(upgraded)) as after:
        old_schema, new_schema = _schema(before), _schema(after)
        old_tables, new_tables = set(_tables(before)), set(_tables(after))
        if (set(new_schema) - set(old_schema) != _CANDIDATE_TABLES | _CANDIDATE_INDEXES
                or new_tables - old_tables != _CANDIDATE_TABLES
                or any(new_schema.get(name) != sql for name, sql in old_schema.items())
                or not old_tables.issubset(new_tables)):
            raise ValueError("SCHEMA19_ADDITIVE_MISMATCH")
        # Check the new objects against the reviewed DDL, not just their names.
        with closing(sqlite3.connect(":memory:")) as model:
            model.execute("CREATE TABLE events (id TEXT PRIMARY KEY)")
            model.executescript(SENSORY_EPISODE_QUEUE_CANDIDATE_SQL)
            expected = _schema(model)
        if any(new_schema[name] != expected[name] for name in _CANDIDATE_TABLES | _CANDIDATE_INDEXES):
            raise ValueError("SCHEMA19_DDL_MISMATCH")
        for name in sorted(old_tables - {"schema_migrations"}):
            if _row_hashes(before, name) != _row_hashes(after, name):
                raise ValueError("SCHEMA19_DATA_MISMATCH")
        old_history = before.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
        new_history = after.execute("SELECT * FROM schema_migrations ORDER BY version").fetchall()
        if (not old_history or old_history[-1][0] != 18 or new_history[:-1] != old_history
                or len(new_history) != len(old_history) + 1 or new_history[-1][0] != 19):
            raise ValueError("SCHEMA19_HISTORY_MISMATCH")
        if any(after.execute("SELECT COUNT(*) FROM " + name).fetchone()[0] for name in _CANDIDATE_TABLES):
            raise ValueError("SCHEMA19_NEW_TABLE_NOT_EMPTY")
        return {"old_tables": len(old_tables), "preserved_tables": len(old_tables) - 1,
                "new_tables": len(_CANDIDATE_TABLES), "migration_history_preserved": True}


def prepare_schema19_copy(source: Path, output_directory: Path) -> tuple[Path, dict]:
    """Create a private candidate snapshot without changing the v18 backup.

    No production connection, credentials, bootstrap, or in-place upgrade.
    An existing directory is never reused or removed.
    """
    source = Path(source).resolve(strict=True)
    output_directory = Path(output_directory)
    original = verify_snapshot(source, expected_schema=18)
    source_archive = Path(str(source) + ".avatar.zip")
    output_directory.mkdir(mode=0o700, exist_ok=False)
    try:
        target = output_directory / "schema19.db"
        archive = Path(str(target) + ".avatar.zip")
        shutil.copyfile(source, target)
        shutil.copyfile(source_archive, archive)
        target.chmod(0o600)
        archive.chmod(0o600)
        if _sha(target) != original["database_sha256"] or _sha(archive) != original["archive_sha256"]:
            raise ValueError("SCHEMA19_SOURCE_CHANGED")
        with closing(sqlite3.connect(target)) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            try:
                for sql in statements(SENSORY_EPISODE_QUEUE_CANDIDATE_SQL):
                    conn.execute(sql)
                conn.execute("INSERT INTO schema_migrations(version) VALUES (19)")
                if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
                    raise ValueError("SCHEMA19_FOREIGN_KEYS")
                conn.commit()
            except BaseException:
                conn.rollback()
                raise
        report = verify_snapshot(target, expected_schema=19)
        report.update(compare_upgrade(source, target))
        if (_sha(source) != original["database_sha256"]
                or _sha(source_archive) != original["archive_sha256"]
                or report["archive_sha256"] != original["archive_sha256"]):
            raise ValueError("SCHEMA19_SOURCE_CHANGED")
        report["source_schema_version"] = 18
        return target, report
    except BaseException:
        shutil.rmtree(output_directory)
        raise


def main(argv=None):
    """Explicit operator path, separate from the certified PCS.4C v18 panel."""
    import argparse
    import json

    from .pcs4_recovery import certify_restore, destination_from_environment
    from .pcs4_restart import certify_restart

    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="action", required=True)
    prepare = commands.add_parser("prepare")
    prepare.add_argument("snapshot", type=Path)
    prepare.add_argument("output_directory", type=Path)
    verify = commands.add_parser("verify")
    verify.add_argument("snapshot", type=Path)
    for action in ("restore", "restart"):
        command = commands.add_parser(action)
        command.add_argument("snapshot", type=Path)
        command.add_argument("--isolated-destination", action="store_true", required=True)
    args = parser.parse_args(argv)
    try:
        if args.action == "prepare":
            _, report = prepare_schema19_copy(args.snapshot, args.output_directory)
        elif args.action == "verify":
            report = verify_snapshot(args.snapshot, expected_schema=19)
        elif args.action == "restore":
            report = certify_restore(args.snapshot, destination_from_environment(), expected_schema=19)
        else:
            report = certify_restart(args.snapshot, destination_from_environment(), expected_schema=19)
        # Keep hashes in the private snapshot verification routines, not logs.
        report = {key: value for key, value in report.items()
                  if key not in {"database_sha256", "archive_sha256"}}
        report.update(status="ok", action=args.action, runtime_schema=18)
    except Exception as exc:
        report = {"status": "error", "action": args.action, "exception_type": type(exc).__name__,
                  "runtime_schema": 18}
        reason = str(exc)
        if type(exc) is ValueError and reason in {
            "SCHEMA19_ADDITIVE_MISMATCH", "SCHEMA19_DDL_MISMATCH", "SCHEMA19_DATA_MISMATCH",
            "SCHEMA19_HISTORY_MISMATCH", "SCHEMA19_NEW_TABLE_NOT_EMPTY", "SCHEMA19_SOURCE_CHANGED",
            "SCHEMA19_FOREIGN_KEYS", "PCS4C_SCHEMA", "PCS4C_INTEGRITY", "PCS4C_FOREIGN_KEYS",
            "PCS4C_ASSET_MANIFEST", "PCS4C_ASSET_MEMBERS", "PCS4C_ASSET_SIZE", "PCS4C_ASSET_HASH",
            "PCS4C_SQL_ASSET_HASH", "PCS4C_DESTINATION_IS_SOURCE", "PCS4C_DESTINATION_TOKEN_REUSED",
            "PCS4C_MISSING_CONFIG", "PCS4C_SCHEMA_MISMATCH", "PCS4C_DATA_MISMATCH",
            "PCS4C_RESTART_TABLES", "PCS4C_RESTART_RANK_MISMATCH", "PCS4C_RESTART_EMPTY_RECALL",
            "PCS4C_RESTART_MEMORY_CHANGED", "PCS4C_RESTART_EPISODE_WORK_CHANGED", "PCS4C_SOURCE_CHANGED",
        }:
            report["reason"] = reason
    print("SCHEMA19_DRILL " + json.dumps(report, separators=(",", ":")))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
