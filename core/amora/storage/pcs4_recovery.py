"""Operator-only PCS.4C recovery drill. Never called by the live API.

Run in an isolated job with a private, durable backup copy. No Core bootstrap,
provider calls, source writes or output of records, paths, URLs or tokens.
"""
from __future__ import annotations

import argparse
from collections import Counter
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import zipfile
from urllib.parse import urlparse

from .recovery import restore_empty_remote
from .remote import LibSQLBackend
from .schema import LATEST_SCHEMA_VERSION


def _quote(name):
    return '"' + name.replace('"', '""') + '"'


def _open(path):
    return sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)


def _sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _schema(db):
    return dict(db.execute(
        "SELECT name,sql FROM sqlite_schema WHERE sql IS NOT NULL "
        "AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'memory_fts_%'"
    ))


def _tables(db):
    return [row[0] for row in db.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' "
        "AND (name NOT LIKE 'sqlite_%' OR name='sqlite_sequence') "
        "AND name NOT LIKE 'memory_fts_%' ORDER BY name"
    )]


def verify_snapshot(snapshot, *, expected_schema=LATEST_SCHEMA_VERSION):
    """Verify both files, including sidecar even when SQL blobs are present."""
    if type(expected_schema) is not int or expected_schema not in {LATEST_SCHEMA_VERSION, 19}:
        raise ValueError("PCS4C_SCHEMA")
    snapshot = Path(snapshot)
    archive_path = Path(str(snapshot) + ".avatar.zip")
    with closing(_open(snapshot)) as db, zipfile.ZipFile(archive_path) as archive:
        if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise ValueError("PCS4C_INTEGRITY")
        if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ValueError("PCS4C_FOREIGN_KEYS")
        version = db.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        if version != expected_schema:
            raise ValueError("PCS4C_SCHEMA")
        if expected_schema == 19:
            from .sensory_episode_schema import verify_candidate_ddl
            verify_candidate_ddl(db)
        assets = [
            dict(zip(("id", "file_name", "size_bytes", "sha256"), row))
            for row in db.execute("SELECT id,file_name,size_bytes,sha256 FROM avatar_assets")
        ]
        manifest = json.loads(archive.read("manifest.json"))
        entries = manifest.get("assets")
        if (manifest.get("version") != 1 or not isinstance(entries, list)
                or sorted(entries, key=lambda x: x["id"]) != sorted(assets, key=lambda x: x["id"])):
            raise ValueError("PCS4C_ASSET_MANIFEST")
        names = ["manifest.json"] + ["assets/" + x["file_name"] for x in assets]
        if Counter(archive.namelist()) != Counter(names):
            raise ValueError("PCS4C_ASSET_MEMBERS")
        for asset in assets:
            name = asset["file_name"]
            member = archive.getinfo("assets/" + name)
            if member.file_size != asset["size_bytes"]:
                raise ValueError("PCS4C_ASSET_SIZE")
            with archive.open(member) as stream:
                if hashlib.file_digest(stream, "sha256").hexdigest() != asset["sha256"]:
                    raise ValueError("PCS4C_ASSET_HASH")
            row = db.execute("SELECT body FROM avatar_asset_blobs WHERE file_name=?", (name,)).fetchone()
            if row is None or len(row[0]) != asset["size_bytes"] or hashlib.sha256(row[0]).hexdigest() != asset["sha256"]:
                raise ValueError("PCS4C_SQL_ASSET_HASH")
        tables = _tables(db)
        rows = sum(db.execute("SELECT COUNT(*) FROM " + _quote(table)).fetchone()[0] for table in tables)
    return {"schema_version": version, "tables": len(tables), "rows": rows,
            "assets": len(assets), "database_bytes": snapshot.stat().st_size,
            "archive_bytes": archive_path.stat().st_size,
            "database_sha256": _sha(snapshot), "archive_sha256": _sha(archive_path)}


def _row_hashes(db, table):
    # SQLite values are scalar types (including bytes). repr(tuple) preserves
    # types and boundaries; multiset comparison preserves duplicate rows.
    return Counter(hashlib.sha256(repr(tuple(row)).encode("utf-8")).digest()
                   for row in db.execute("SELECT * FROM " + _quote(table)))


def compare_snapshots(source, recovered):
    """Compare all logical rows and schema, not physical SQLite page layout."""
    with closing(_open(Path(source))) as a, closing(_open(Path(recovered))) as b:
        if _schema(a) != _schema(b) or _tables(a) != _tables(b):
            raise ValueError("PCS4C_SCHEMA_MISMATCH")
        tables = _tables(a)
        for table in tables:
            if _row_hashes(a, table) != _row_hashes(b, table):
                raise ValueError("PCS4C_DATA_MISMATCH")
        return len(tables)


def _origin_host(url):
    # Use the same production parser/validation as the canonical backend.
    return urlparse(LibSQLBackend(url, "validation-only").url).hostname.rstrip(".").lower()


def destination_from_environment():
    source = os.environ.get("AMORA_LIBSQL_URL", "")
    destination = os.environ.get("AMORA_PCS4C_DESTINATION_URL", "")
    token = os.environ.get("AMORA_PCS4C_DESTINATION_TOKEN", "")
    if not source or not destination or not token:
        raise ValueError("PCS4C_MISSING_CONFIG")
    if _origin_host(source) == _origin_host(destination):
        raise ValueError("PCS4C_DESTINATION_IS_SOURCE")
    if token == os.environ.get("AMORA_SECRET_LIBSQL_AUTH_TOKEN"):
        raise ValueError("PCS4C_DESTINATION_TOKEN_REUSED")
    return LibSQLBackend(destination, token)


def certify_restore(snapshot, backend, *, expected_schema=LATEST_SCHEMA_VERSION):
    """Import into empty isolated destination, re-export and compare.

    A failed comparison leaves the isolated database intact for diagnosis;
    the operator must provision a fresh empty destination before retrying.
    """
    snapshot = Path(snapshot)
    report = verify_snapshot(snapshot, expected_schema=expected_schema)
    restore_empty_remote(backend, snapshot, target_schema=expected_schema)
    with tempfile.TemporaryDirectory(prefix="pcs4c-") as directory:
        recovered = Path(directory) / "recovered.db"
        with closing(backend.connect(recovered)) as connection:
            backend.backup(connection, recovered)
        report["equal_tables"] = compare_snapshots(snapshot, recovered)
    if _sha(snapshot) != report["database_sha256"] or _sha(Path(str(snapshot) + ".avatar.zip")) != report["archive_sha256"]:
        raise ValueError("PCS4C_SOURCE_CHANGED")
    return report


def review_restore_destination(snapshot, backend, *, expected_schema=LATEST_SCHEMA_VERSION):
    """Read-only reconciliation: empty, exactly restored, or fail closed."""
    snapshot = Path(snapshot)
    report = verify_snapshot(snapshot, expected_schema=expected_schema)
    with tempfile.TemporaryDirectory(prefix="pcs4c-review-") as directory:
        recovered = Path(directory) / "recovered.db"
        with closing(backend.connect(recovered)) as connection:
            with connection.read_transaction():
                count = connection.execute(
                    "SELECT COUNT(*) FROM sqlite_schema WHERE name NOT LIKE 'sqlite_%'"
                ).fetchone()[0]
            if count:
                class ReadSnapshot:
                    def __enter__(self):
                        self.transaction = connection.read_transaction()
                        self.transaction.__enter__()
                        return self

                    def __exit__(self, *args):
                        return self.transaction.__exit__(*args)

                    def execute(self, sql, parameters=()):
                        if not sql.lstrip().upper().startswith("SELECT "):
                            raise ValueError("PCS4C_REVIEW_READ_ONLY")
                        return connection.execute(sql, parameters)

                backend.backup(ReadSnapshot(), recovered)
                report["equal_tables"] = compare_snapshots(snapshot, recovered)
    if (_sha(snapshot) != report["database_sha256"]
            or _sha(Path(str(snapshot) + ".avatar.zip")) != report["archive_sha256"]):
        raise ValueError("PCS4C_SOURCE_CHANGED")
    return ("exact" if count else "empty"), report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("verify", "restore"))
    parser.add_argument("snapshot", type=Path)
    parser.add_argument("--isolated-empty-destination", action="store_true")
    args = parser.parse_args(argv)
    try:
        if args.mode == "restore":
            if not args.isolated_empty_destination:
                raise ValueError("PCS4C_CONFIRM_ISOLATION")
            report = certify_restore(args.snapshot, destination_from_environment())
        else:
            report = verify_snapshot(args.snapshot)
        report.update(status="ok", mode=args.mode, runtime_projection_enabled=False)
    except Exception as exc:
        report = {"status": "error", "mode": args.mode,
                  "exception_type": type(exc).__name__, "runtime_projection_enabled": False}
        # Emit only our fixed codes, never arbitrary exception text.
        if type(exc) is ValueError and len(exc.args) == 1 and exc.args[0] in {
            "PCS4C_INTEGRITY", "PCS4C_FOREIGN_KEYS", "PCS4C_SCHEMA",
            "PCS4C_ASSET_MANIFEST", "PCS4C_ASSET_MEMBERS", "PCS4C_ASSET_SIZE",
            "PCS4C_ASSET_HASH", "PCS4C_SQL_ASSET_HASH", "PCS4C_SCHEMA_MISMATCH",
            "PCS4C_DATA_MISMATCH", "PCS4C_MISSING_CONFIG", "PCS4C_DESTINATION_IS_SOURCE",
            "PCS4C_DESTINATION_TOKEN_REUSED", "PCS4C_SOURCE_CHANGED", "PCS4C_CONFIRM_ISOLATION",
        }:
            report["reason"] = exc.args[0]
    print("PCS4C_RECOVERY " + json.dumps(report, separators=(",", ":")))
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
