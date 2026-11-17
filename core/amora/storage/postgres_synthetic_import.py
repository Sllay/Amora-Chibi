"""Copy synthetic SQLite v18 rows to disposable local PostgreSQL 18 only.

Not a production importer. This module has no DSN/config, no file paths,
no network client and no Core backend selector. It refuses all non-loopback
PostgreSQL connections and nonempty lab targets. All data is inserted in
one transaction with deferred FK checks, then compared by typed row digests.
Private PCS.4C backups must NOT be sent to this laboratory.
"""
from __future__ import annotations

from collections import Counter
from contextlib import closing
import hashlib
import sqlite3

from .postgres_memory_lab import ensure_local_lab
from .postgres_v18_lab_schema import _q, _ref, LAB_SCHEMA
from .postgres_v18_manifest import (
    build_manifest, synthetic_v18_connection, _source_tables,
)

_BATCH = 100


class SyntheticImportError(ValueError):
    """Failure codes, never record payloads or private database paths."""


def _digest_rows(rows) -> Counter[bytes]:
    # Preserve exact SQLite scalar types (TEXT/INTEGER/REAL/BLOB/NULL).
    # No real records or digests are ever printed or exported.
    return Counter(
        hashlib.sha256(repr(tuple(row)).encode("utf-8")).digest()
        for row in rows
    )


def _validate_source(source: sqlite3.Connection, manifest: dict) -> None:
    if source.execute("PRAGMA integrity_check").fetchone() != ("ok",):
        raise SyntheticImportError("SYNTHETIC_SQLITE_INTEGRITY")
    if source.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise SyntheticImportError("SYNTHETIC_SQLITE_FOREIGN_KEYS")
    version = source.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
    if version != (18,):
        raise SyntheticImportError("SYNTHETIC_SOURCE_VERSION")
    names = {row[0] for row in _source_tables(source)}
    if names != {table["name"] for table in manifest["tables"]}:
        raise SyntheticImportError("SYNTHETIC_SOURCE_TABLES")
    with closing(synthetic_v18_connection()) as reference:
        for table in manifest["tables"]:
            name = table["name"]
            source_cols = [
                (row[1], row[2].upper(), row[3], row[4], row[5])
                for row in source.execute("PRAGMA table_info(" + _q(name) + ")")
            ]
            expected_cols = [
                (row[1], row[2].upper(), row[3], row[4], row[5])
                for row in reference.execute("PRAGMA table_info(" + _q(name) + ")")
            ]
            if source_cols != expected_cols:
                raise SyntheticImportError("SYNTHETIC_SOURCE_COLUMNS")


def _copy_rows(source, destination, table: str, columns: list[str]) -> int:
    quoted = ", ".join(_q(column) for column in columns)
    bind = ", ".join("%s" for _ in columns)
    insert = f"INSERT INTO {_ref(table)} ({quoted}) VALUES ({bind})"
    reader = source.execute("SELECT " + quoted + " FROM " + _q(table))
    count = 0
    with destination.cursor() as cursor:
        while batch := reader.fetchmany(_BATCH):
            cursor.executemany(insert, batch)
            count += len(batch)
    return count


def _assert_equal(source, destination, source_table: str, dest_table: str,
                  columns: list[str]) -> None:
    quoted = ", ".join(_q(c) for c in columns)
    original = source.execute("SELECT " + quoted + " FROM " + _q(source_table))
    copied = destination.execute("SELECT " + quoted + " FROM " + _ref(dest_table))
    if _digest_rows(original) != _digest_rows(copied):
        raise SyntheticImportError("SYNTHETIC_ROW_PARITY_FAILED")


def import_synthetic_v18(source: sqlite3.Connection, destination) -> dict[str, object]:
    """One atomic import and exact typed-row comparison, for CI fixtures only."""
    # Guard before *any* PostgreSQL SQL, even reads.
    ensure_local_lab(destination)
    manifest = build_manifest()
    _validate_source(source, manifest)
    ordinary = [t for t in manifest["tables"] if t["kind"] == "ordinary"]
    expected = [t["name"] for t in ordinary] + ["memory_search"]
    existing = {r[0] for r in destination.execute("""
        SELECT tablename FROM pg_tables WHERE schemaname=%s
    """, (LAB_SCHEMA,))}
    if existing != set(expected):
        raise SyntheticImportError("SYNTHETIC_TARGET_SCHEMA_MISMATCH")
    for table in expected:
        if destination.execute(
            "SELECT 1 FROM " + _ref(table) + " LIMIT 1"
        ).fetchone() is not None:
            raise SyntheticImportError("SYNTHETIC_TARGET_NOT_EMPTY")

    total = 0
    fts_total = 0
    identities = 0
    with destination.transaction():
        # Allow self-references and SQLite's historical insert ordering while
        # preserving FK enforcement at COMMIT. Runtime defaults remain immediate.
        destination.execute("SET CONSTRAINTS ALL DEFERRED")
        for item in ordinary:
            columns = [c["name"] for c in item["columns"]]
            count = _copy_rows(source, destination, item["name"], columns)
            total += count
            _assert_equal(source, destination, item["name"], item["name"], columns)

        # FTS5 logical content, not shadow tables. Rebuildable search document.
        fts_columns = ["memory_id", "content", "tags"]
        fts_total = _copy_rows_to_search(source, destination, fts_columns)
        _assert_equal(source, destination, "memory_fts", "memory_search", fts_columns)

        source_seq = dict(source.execute("SELECT name,seq FROM sqlite_sequence"))
        for item in ordinary:
            if "AUTOINCREMENT" not in item["sqlite_create_sql"].upper():
                continue
            pks = [c for c in item["columns"] if c["pk_ordinal"]]
            if len(pks) != 1:
                raise SyntheticImportError("SYNTHETIC_IDENTITY_SCHEMA")
            column = pks[0]["name"]
            highwater = destination.execute(
                "SELECT COALESCE(MAX(" + _q(column) + "),0) FROM " + _ref(item["name"])
            ).fetchone()[0]
            target = max(highwater, source_seq.get(item["name"], 0))
            if target:
                name = destination.execute(
                    "SELECT pg_get_serial_sequence(%s,%s)",
                    (f"{LAB_SCHEMA}.{item['name']}", column),
                ).fetchone()[0]
                if not name:
                    raise SyntheticImportError("SYNTHETIC_IDENTITY_MISSING")
                destination.execute(
                    "SELECT setval(%s::regclass,%s,true)", (name, target)
                )
                identities += 1

        # Recheck counts within transaction; no partially certified result.
        for item in ordinary:
            name = item["name"]
            original = source.execute(
                "SELECT COUNT(*) FROM " + _q(name)
            ).fetchone()[0]
            remote = destination.execute(
                "SELECT COUNT(*) FROM " + _ref(name)
            ).fetchone()[0]
            if original != remote:
                raise SyntheticImportError("SYNTHETIC_COUNT_PARITY_FAILED")
        if destination.execute(
            "SELECT COUNT(*) FROM " + _ref("memory_search")
        ).fetchone()[0] != fts_total:
            raise SyntheticImportError("SYNTHETIC_FTS_COUNT_PARITY_FAILED")

    return {
        "status": "ok",
        "mode": "synthetic-local-only",
        "relational_tables": len(ordinary),
        "relational_rows": total,
        "fts_rows": fts_total,
        "identity_sequences_reconciled": identities,
        "schema_version": 18,
        "typed_row_parity": True,
        "target_was_empty": True,
        "real_backup_read": False,
        "production_backend_changed": False,
    }


def _copy_rows_to_search(source, destination, columns: list[str]) -> int:
    quoted = ", ".join(_q(c) for c in columns)
    reader = source.execute("SELECT " + quoted + " FROM memory_fts")
    insert = ("INSERT INTO " + _ref("memory_search")
              + " (" + quoted + ") VALUES (%s,%s,%s)")
    count = 0
    with destination.cursor() as cursor:
        while batch := reader.fetchmany(_BATCH):
            cursor.executemany(insert, batch)
            count += len(batch)
    return count
