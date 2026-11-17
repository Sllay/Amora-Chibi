"""Deterministic, synthetic-only schema v18 inventory for PostgreSQL migration.

Builds an in-memory SQLite schema from canonical Amora migrations. The manifest
contains structure, never production records, paths, hashes or credentials.
It is an input to *reviewed* PostgreSQL DDL, NOT a SQL string converter.
"""
from __future__ import annotations

from contextlib import closing
import hashlib
import json
import sqlite3

from .migrations import MIGRATIONS
from .migrations_v06 import MIGRATION_V06
from .migrations_v07 import MIGRATION_V07
from .migrations_v08 import MIGRATION_V08
from .migrations_v09 import MIGRATION_V09
from .migrations_v10 import MIGRATION_V10
from .migrations_v101 import MIGRATION_V101
from .migrations_v102 import MIGRATION_V102
from .migrations_v103 import MIGRATION_V103
from .migrations_v104 import MIGRATION_V104
from .migrations_v105 import MIGRATION_V105
from .migrations_v106 import MIGRATION_V106
from .migrations_v107 import MIGRATION_V107
from .migrations_v108 import MIGRATION_V108

V18_MIGRATIONS = (*MIGRATIONS, MIGRATION_V06, MIGRATION_V07,
                  MIGRATION_V08, MIGRATION_V09, MIGRATION_V10,
                  MIGRATION_V101, MIGRATION_V102, MIGRATION_V103,
                  MIGRATION_V104, MIGRATION_V105, MIGRATION_V106,
                  MIGRATION_V107, MIGRATION_V108)
V18_VERSION = 18
TYPE_POLICY = {
    "TEXT": "text",
    "INTEGER": "bigint",
    "REAL": "double precision",
    "BLOB": "bytea",
}
_SQLITE_SYSTEM = "sqlite_sequence"
_FTS_SQL = """
    CREATE VIRTUAL TABLE memory_fts USING fts5(
        memory_id UNINDEXED, content, tags,
        tokenize='unicode61 remove_diacritics 2'
    )
"""


class ManifestError(ValueError):
    """Never fall back to an incomplete inferred schema."""


def _ident(name: str) -> str:
    # Identifier quoting for PRAGMA lookup, never SQL translation.
    return '"' + name.replace('"', '""') + '"'


def synthetic_v18_connection() -> sqlite3.Connection:
    """No disk, no network: recreate the exact source migration sequence."""
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("""
            CREATE TABLE schema_migrations (
                version INTEGER PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
        """)
        versions = [version for version, _ in V18_MIGRATIONS]
        if versions != list(range(1, V18_VERSION + 1)):
            raise ManifestError("V18_MIGRATION_SEQUENCE_CHANGED")
        for version, script in V18_MIGRATIONS:
            conn.executescript(script)
            conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
        conn.execute(_FTS_SQL)
        if conn.execute("PRAGMA integrity_check").fetchone() != ("ok",):
            raise ManifestError("V18_SYNTHETIC_INTEGRITY_FAILED")
        if conn.execute("PRAGMA foreign_key_check").fetchone() is not None:
            raise ManifestError("V18_SYNTHETIC_FOREIGN_KEYS_FAILED")
        return conn
    except BaseException:
        conn.close()
        raise


def _source_tables(conn: sqlite3.Connection):
    # This table filter is identical to the PCS4C source verifier:
    # FTS5 shadow structures are not logical tables; sqlite_sequence is.
    return conn.execute("""
        SELECT name, sql FROM sqlite_schema
        WHERE type='table'
          AND (name NOT LIKE 'sqlite_%' OR name='sqlite_sequence')
          AND name NOT LIKE 'memory_fts_%'
        ORDER BY name
    """).fetchall()


def _column_inventory(conn: sqlite3.Connection, table: str) -> list[dict]:
    cols = []
    for cid, name, declared, required, default, primary_key, hidden in conn.execute(
        f"PRAGMA table_xinfo({_ident(table)})"
    ):
        # Hidden virtual FTS meta columns are not user data fields.
        if hidden:
            continue
        normalized = (declared or "").upper()
        if table not in (_SQLITE_SYSTEM, "memory_fts") and normalized not in TYPE_POLICY:
            raise ManifestError("V18_UNREVIEWED_SOURCE_TYPE")
        cols.append({
            "name": name,
            "sqlite_type": normalized,
            "postgres_type": TYPE_POLICY.get(normalized),
            "not_null": bool(required),
            "default_sqlite": default,
            "pk_ordinal": primary_key,
        })
    return cols


def _foreign_keys(conn: sqlite3.Connection, table: str) -> list[dict]:
    groups: dict[int, dict] = {}
    for identity, ordinal, parent, source, target, update, delete, match in conn.execute(
        f"PRAGMA foreign_key_list({_ident(table)})"
    ):
        item = groups.setdefault(identity, {
            "parent": parent,
            "columns": [],
            "referenced_columns": [],
            "on_update": update,
            "on_delete": delete,
            "match": match,
        })
        item["columns"].append((ordinal, source))
        item["referenced_columns"].append((ordinal, target))
    return [
        {
            **{key: value for key, value in group.items()
               if key not in ("columns", "referenced_columns")},
            "columns": [name for _, name in sorted(group["columns"])],
            "referenced_columns": [name for _, name in sorted(group["referenced_columns"])],
        }
        for _, group in sorted(groups.items())
    ]


def _unique_constraints(conn: sqlite3.Connection, table: str) -> list[dict]:
    unique = []
    for _, index_name, is_unique, origin, partial in conn.execute(
        f"PRAGMA index_list({_ident(table)})"
    ):
        if not is_unique:
            continue
        fields = [(seq, name, cid) for seq, cid, name, desc, coll, key in conn.execute(
            f"PRAGMA index_xinfo({_ident(index_name)})"
        ) if key]
        if any(cid < 0 or name is None for _, name, cid in fields) or partial:
            raise ManifestError("V18_UNREVIEWED_UNIQUE_EXPRESSION")
        unique.append({
            "columns": [name for _, name, _ in sorted(fields)],
            "origin": origin,
        })
    return sorted(unique, key=lambda c: (c["origin"], c["columns"]))


def build_manifest() -> dict:
    """Return only source structure; PG DDL must be reviewed separately."""
    with closing(synthetic_v18_connection()) as conn:
        source = _source_tables(conn)
        tables = []
        for name, source_sql in source:
            kind = ("sqlite_sequence" if name == _SQLITE_SYSTEM else
                    "fts5" if name == "memory_fts" else "ordinary")
            if kind == "fts5" and "USING fts5" not in (source_sql or ""):
                raise ManifestError("V18_FTS5_DEFINITION_CHANGED")
            tables.append({
                "name": name,
                "kind": kind,
                "columns": _column_inventory(conn, name),
                "foreign_keys": _foreign_keys(conn, name) if kind == "ordinary" else [],
                "unique_constraints": (
                    _unique_constraints(conn, name) if kind == "ordinary" else []
                ),
                # Exact source CREATE statement is useful to review CHECK/DEFAULT
                # clauses. It is never executed against PostgreSQL.
                "sqlite_create_sql": source_sql,
            })
        indexes = [
            {"name": name, "sqlite_create_sql": sql}
            for name, sql in conn.execute("""
                SELECT name, sql FROM sqlite_schema
                WHERE type='index' AND sql IS NOT NULL ORDER BY name
            """)
        ]
        views = conn.execute("SELECT COUNT(*) FROM sqlite_schema WHERE type='view'").fetchone()[0]
        triggers = conn.execute("SELECT COUNT(*) FROM sqlite_schema WHERE type='trigger'").fetchone()[0]
        if len(tables) != 51 or len(indexes) != 65:
            raise ManifestError("V18_LOGICAL_STRUCTURE_DRIFT")
        if sum(t["kind"] == "fts5" for t in tables) != 1:
            raise ManifestError("V18_FTS5_STRUCTURE_DRIFT")
        if views or triggers:
            raise ManifestError("V18_UNREVIEWED_TRIGGERS_OR_VIEWS")
        if [t["name"] for t in tables if t["kind"] == "sqlite_sequence"] != ["sqlite_sequence"]:
            raise ManifestError("V18_SEQUENCE_STRUCTURE_DRIFT")
        result = {
            "schema_version": V18_VERSION,
            "source": "synthetic-canonical-migrations",
            "sqlite_logical_table_count": len(tables),
            "postgres_relational_table_count": sum(t["kind"] == "ordinary" for t in tables),
            "source_fts5_table_count": 1,
            "sqlite_sequence_table_count": 1,
            "explicit_index_count": len(indexes),
            "views": views,
            "triggers": triggers,
            "tables": tables,
            "indexes": indexes,
        }
        result["structural_sha256"] = hashlib.sha256(
            json.dumps(result, ensure_ascii=False, sort_keys=True,
                       separators=(",", ":")).encode()
        ).hexdigest()
        return result


def sanitized_summary() -> dict[str, int | str]:
    manifest = build_manifest()
    return {
        key: value for key, value in manifest.items()
        if key in {
            "schema_version", "source", "sqlite_logical_table_count",
            "postgres_relational_table_count", "source_fts5_table_count",
            "sqlite_sequence_table_count", "explicit_index_count", "views",
            "triggers",
        }
    }
