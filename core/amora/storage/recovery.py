"""Explicit offline import into an EMPTY remote destination.

Never reset, merge or replace a live database. Caller controls credentials and
must stop the source runtime while moving the final snapshot/asset archive.
"""

import hashlib
import sqlite3
import zipfile
from pathlib import Path


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
                for values in source.execute("SELECT * FROM " + quoted):
                    target.execute(
                        "INSERT INTO "
                        + quoted
                        + " VALUES ("
                        + ",".join("?" for _ in values)
                        + ")",
                        tuple(values),
                    )
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
            from .remote import statements

            version = target.execute(
                "SELECT MAX(version) FROM schema_migrations"
            ).fetchone()[0]
            if version not in {10, 11, 12, 13, 14}:
                raise ValueError("only audited schema 10/11/12/13/14 snapshots can be imported")
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
            if target.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise ValueError("restored constraints failed")
    finally:
        source.close()
        target.close()
