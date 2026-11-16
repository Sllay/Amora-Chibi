"""Explicit offline import into an EMPTY remote destination.

Never reset, merge or replace a live database. Caller controls credentials and
must stop the source runtime while moving the final snapshot/asset archive.
"""

import hashlib
import json
import sys
import sqlite3
import zipfile
from pathlib import Path

from .remote import _encode


_RESTORE_BATCH_ROWS = 100
_RESTORE_BATCH_BYTES = 512 * 1024


def _restore_batches(sql, rows):
    """Bound Hrana pipeline size; retain the existing single-row path for large blobs."""
    batch, size = [], 0
    for row in rows:
        values = tuple(row)
        request = {"type": "execute", "stmt": {"sql": sql,
                   "args": [_encode(value) for value in values], "want_rows": True}}
        # ASCII JSON is conservative for non-ASCII strings; reserve envelope overhead.
        weight = len(json.dumps(request, ensure_ascii=True).encode("utf-8")) + 16
        if batch and (len(batch) >= _RESTORE_BATCH_ROWS or size + weight > _RESTORE_BATCH_BYTES):
            yield batch
            batch, size = [], 0
        batch.append(values)
        size += weight
    if batch:
        yield batch


def _restore_progress(phase, tables, rows):
    print(f"PCS4C_RESTORE_PROGRESS phase={phase} tables={tables} rows={rows}",
          file=sys.stderr, flush=True)


def restore_empty_remote(backend, snapshot: Path) -> None:
    source = sqlite3.connect(f"file:{snapshot}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    target = backend.connect(Path("unused"))
    try:
        if (
            source.execute("PRAGMA integrity_check").fetchone()[0] != "ok"
            or source.execute("PRAGMA foreign_key_check").fetchone() is not None
        ):
            raise ValueError("source snapshot integrity failed")
        schema = source.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_schema WHERE sql IS NOT NULL ORDER BY rowid"
        ).fetchall()
        with target:
            if target.execute(
                "SELECT COUNT(*) FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0]:
                raise ValueError("destination is not empty; refusing restore")
            target.execute("PRAGMA defer_foreign_keys=ON")
            tables_done = rows_done = 0
            _restore_progress("import", tables_done, rows_done)
            for row in schema:
                name, sql = row["name"], row["sql"]
                if (
                    row["type"] != "table"
                    or name.startswith("sqlite_")
                    or name.startswith("memory_fts_")
                ):
                    continue
                if "VIRTUAL TABLE" in sql.upper() and name != "memory_fts":
                    raise ValueError("unsupported virtual table")
                target.execute(sql)
                quoted = '"' + name.replace('"', '""') + '"'
                cursor = source.execute("SELECT * FROM " + quoted)
                insert = "INSERT INTO " + quoted + " VALUES (" + ",".join(
                    "?" for _ in cursor.description
                ) + ")"
                for batch in _restore_batches(insert, cursor):
                    target.executemany(insert, batch)
                    previous = rows_done
                    rows_done += len(batch)
                    if rows_done // 1000 != previous // 1000:
                        _restore_progress("import", tables_done, rows_done)
                tables_done += 1
                _restore_progress("table_complete", tables_done, rows_done)
            if any(row["name"] == "sqlite_sequence" for row in schema):
                target.execute("DELETE FROM sqlite_sequence")
                for row in source.execute("SELECT name,seq FROM sqlite_sequence"):
                    target.execute(
                        "INSERT INTO sqlite_sequence(name,seq) VALUES (?,?)", tuple(row)
                    )
            for row in schema:
                if row["type"] in {"index", "trigger", "view"} and not row[
                    "tbl_name"
                ].startswith("memory_fts_"):
                    target.execute(row["sql"])

            # Audited older snapshots are upgraded additively in the empty target;
            # source bytes are never modified in place.
            from .migrations_v101 import MIGRATION_V101
            from .migrations_v102 import MIGRATION_V102
            from .migrations_v103 import MIGRATION_V103
            from .migrations_v104 import MIGRATION_V104
            from .migrations_v105 import MIGRATION_V105
            from .migrations_v106 import MIGRATION_V106
            from .migrations_v107 import MIGRATION_V107
            from .migrations_v108 import MIGRATION_V108
            from .remote import statements
            from .schema import AUDITED_REMOTE_IMPORT_SCHEMA_VERSIONS, LATEST_SCHEMA_VERSION

            version = target.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            if version not in AUDITED_REMOTE_IMPORT_SCHEMA_VERSIONS:
                allowed = "/".join(str(item) for item in sorted(AUDITED_REMOTE_IMPORT_SCHEMA_VERSIONS))
                raise ValueError(f"only audited schema {allowed} snapshots can be imported")
            if version == 10:
                for sql in statements(MIGRATION_V101[1]):
                    target.execute(sql)
                target.execute("INSERT INTO schema_migrations(version) VALUES (11)")
                version = 11
            if version == 11:
                for sql in statements(MIGRATION_V102[1]):
                    target.execute(sql)
                target.execute("INSERT INTO schema_migrations(version) VALUES (12)")
                version = 12
            if version == 12:
                for sql in statements(MIGRATION_V103[1]):
                    target.execute(sql)
                target.execute("INSERT INTO schema_migrations(version) VALUES (13)")
                version = 13
            if version == 13:
                for sql in statements(MIGRATION_V104[1]):
                    target.execute(sql)
                target.execute("INSERT INTO schema_migrations(version) VALUES (14)")
                version = 14
            if version == 14:
                for sql in statements(MIGRATION_V105[1]):
                    target.execute(sql)
                target.execute("INSERT INTO schema_migrations(version) VALUES (15)")
                version = 15
            if version == 15:
                for sql in statements(MIGRATION_V106[1]):
                    target.execute(sql)
                target.execute("INSERT INTO schema_migrations(version) VALUES (16)")
                version = 16
            if version == 16:
                for sql in statements(MIGRATION_V107[1]):
                    target.execute(sql)
                target.execute("INSERT INTO schema_migrations(version) VALUES (17)")
                version = 17
            if version == 17:
                for sql in statements(MIGRATION_V108[1]):
                    target.execute(sql)
                target.execute("INSERT INTO schema_migrations(version) VALUES (18)")
                version = 18
            if version != LATEST_SCHEMA_VERSION:
                raise ValueError(
                    f"snapshot upgrade stopped at schema {version}; expected {LATEST_SCHEMA_VERSION}"
                )

            assets = source.execute("SELECT * FROM avatar_assets").fetchall()
            archive_path = Path(str(snapshot) + ".avatar.zip")
            for info in assets:
                existing = target.execute(
                    "SELECT body FROM avatar_asset_blobs WHERE file_name=?",
                    (info["file_name"],),
                ).fetchone()
                if existing is None:
                    with zipfile.ZipFile(archive_path) as archive:
                        body = archive.read("assets/" + info["file_name"])
                    if (
                        len(body) != info["size_bytes"]
                        or hashlib.sha256(body).hexdigest() != info["sha256"]
                    ):
                        raise ValueError("asset archive integrity failed")
                    target.execute(
                        "INSERT INTO avatar_asset_blobs VALUES (?,?)",
                        (info["file_name"], body),
                    )
                elif (
                    len(existing[0]) != info["size_bytes"]
                    or hashlib.sha256(existing[0]).hexdigest() != info["sha256"]
                ):
                    raise ValueError("snapshot blob integrity failed")
            _restore_progress("validate", tables_done, rows_done)
            if target.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("restored constraints failed")
        _restore_progress("committed", tables_done, rows_done)
    finally:
        source.close()
        target.close()