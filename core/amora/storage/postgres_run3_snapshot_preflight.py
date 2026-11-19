"""Operator-only, offline Run 3 preflight of a private PCS.4C snapshot.

No network, no PostgreSQL connection, no import and no serving Core bootstrap.
This is deliberately separate from the synthetic importer. It reuses the
existing PCS.4C integrity/sidecar verifier, checks immutable source bytes
and compares only schema DDL against the canonical v18 migration fixture.

Only sanitized aggregate counts are printed. A successful preflight NEVER
authorizes a production PostgreSQL cutover.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import json
import re
from pathlib import Path
import sys

from .pcs4_recovery import _open, _schema, _sha, _tables, verify_snapshot
from .postgres_v18_manifest import synthetic_v18_connection


class Run3SnapshotError(RuntimeError):
    """A stable error code, never a filename, SQL row, hash or driver error."""


def _private_regular(path: Path) -> bool:
    return path.is_absolute() and not path.is_symlink() and path.is_file()



# Only these two fixed, publicly defined v18 objects may differ in
# whitespace/casing of their CREATE statements. Private SQL is never logged.
_FORMAT_ONLY_EXCEPTIONS = frozenset(("memory_fts", "schema_migrations"))
_SQL_TOKENS = re.compile(
    r"""\s+|'(?:''|[^'])*'|"(?:\"\"|[^"])*"|
        \x60(?:\x60\x60|[^\x60])*\x60|
        \[[^\]]*\]|[A-Za-z_][A-Za-z0-9_]*|\d+(?:\.\d+)?|[^\s]""",
    re.X | re.S,
)


def _ddl_tokens(sql: str) -> tuple[str, ...] | None:
    """Preserve literal values and quoted identifiers, ignore SQL whitespace/casing."""
    if not isinstance(sql, str) or any(x in sql for x in ("--", "/*", "*/", "\x00")):
        return None
    tokens = []
    position = 0
    while position < len(sql):
        matched = _SQL_TOKENS.match(sql, position)
        if matched is None:
            return None
        token = matched.group()
        position = matched.end()
        if token.isspace():
            continue
        if token[0] in ("'", '"', "\x60", "["):
            closing = { "'": "'", '"': '"', "\x60": "\x60", "[": "]" }[token[0]]
            if len(token) < 2 or not token.endswith(closing):
                return None
            tokens.append(token)
        else:
            tokens.append(token.casefold())
    return tuple(tokens)


def _v18_schema_equivalent(source, reference) -> bool:
    """Exact canonical DDL except lexically identical known v18 objects."""
    actual, expected = _schema(source), _schema(reference)
    if _tables(source) != _tables(reference) or actual.keys() != expected.keys():
        return False
    for name, expected_sql in expected.items():
        actual_sql = actual[name]
        if actual_sql == expected_sql:
            continue
        if name not in _FORMAT_ONLY_EXCEPTIONS:
            return False
        actual_tokens = _ddl_tokens(actual_sql)
        if actual_tokens is None or actual_tokens != _ddl_tokens(expected_sql):
            return False
        row = reference.execute(
            "SELECT type FROM sqlite_schema WHERE name=?", (name,),
        ).fetchone()
        if row is None or row[0] != "table":
            return False
        for pragma in ("table_xinfo", "foreign_key_list", "index_list"):
            if (source.execute('PRAGMA ' + pragma + '("' + name + '")').fetchall()
                    != reference.execute('PRAGMA ' + pragma + '("' + name + '")').fetchall()):
                return False
    return True


def inspect_private_snapshot(snapshot: str | Path) -> dict[str, object]:
    """Read-only inspection for an operator-supplied private v18 snapshot.

    Snapshot and sidecar must already exist in a private durable volume.
    No contents, identifiers, paths, checksums or memory text leave this
    function. If a source changes during the audit, the result is rejected.
    """
    try:
        path = Path(snapshot)
        sidecar = Path(str(path) + ".avatar.zip")
        if not _private_regular(path) or not _private_regular(sidecar):
            raise Run3SnapshotError("RUN3_SNAPSHOT_PRIVATE_PAIR_REQUIRED")
        before = (_sha(path), _sha(sidecar))
        report = verify_snapshot(path, expected_schema=18)
        with closing(_open(path)) as source, closing(synthetic_v18_connection()) as reference:
            if not _v18_schema_equivalent(source, reference):
                raise Run3SnapshotError("RUN3_SNAPSHOT_V18_DDL_DRIFT")
        after = (_sha(path), _sha(sidecar))
        if (before != after
                or before != (report["database_sha256"], report["archive_sha256"])):
            raise Run3SnapshotError("RUN3_SNAPSHOT_CHANGED_DURING_AUDIT")
        # Keep the source hashes inside this process. Never print or return them.
        return {
            "status": "verified_offline",
            "schema_version": 18,
            "logical_tables": report["tables"],
            "logical_rows": report["rows"],
            "avatar_assets": report["assets"],
            "snapshot_bytes": report["database_bytes"],
            "sidecar_bytes": report["archive_bytes"],
            "structure": "canonical_v18_exact",
            "source_freshness": "not_certified",
            "aiven_capacity": "not_measured",
            "aiven_restore": "not_tested",
            "aiven_tls": "not_tested",
            "production_cutover_authorized": False,
        }
    except Run3SnapshotError:
        raise
    except Exception:
        # sqlite, zipfile, filesystem and JSON exceptions can carry paths,
        # row content, archive member names or other private details.
        raise Run3SnapshotError("RUN3_SNAPSHOT_VERIFICATION_FAILED") from None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Offline PCS.4C v18 structural preflight; never a cutover",
    )
    parser.add_argument("--snapshot", required=True)
    parser.add_argument("--confirm-offline", action="store_true")
    args = parser.parse_args(argv)
    if not args.confirm_offline:
        print(json.dumps({
            "status": "blocked", "code": "RUN3_OPERATOR_CONFIRM_OFFLINE_REQUIRED",
            "production_cutover_authorized": False,
        }, sort_keys=True))
        return 2
    try:
        report = inspect_private_snapshot(args.snapshot)
    except Run3SnapshotError as exc:
        print(json.dumps({
            "status": "blocked", "code": str(exc),
            "production_cutover_authorized": False,
        }, sort_keys=True))
        return 1
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
