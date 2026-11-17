"""PostgreSQL 18 disposable schema candidate, not a serving backend.

Compile reviewed *structural metadata* from a synthetic canonical SQLite v18
schema into PostgreSQL table, FK and index DDL. Every unreviewed construct
fails closed. It never reads PCS4C or creates a connection to Aiven/Turso.

Conversion is limited to an empty CI fixture; semantic parity and actual
data migration require separate gates.
"""
from __future__ import annotations

import re

from .postgres_memory_lab import ensure_local_lab
from .postgres_v18_manifest import ManifestError, build_manifest, synthetic_v18_connection

LAB_SCHEMA = "amora_pg_v18_lab"
_IDENT = re.compile(r"[A-Za-z_][A-Za-z_0-9]*\Z")
_NUMBER = re.compile(r"-?(?:0|[1-9]\d*)(?:\.\d+)?\Z")
_TEXT_LITERAL = re.compile(r"'(?:[^']|'')*'\Z")


def _q(value: str) -> str:
    if not _IDENT.fullmatch(value):
        raise ManifestError("V18_UNSAFE_IDENTIFIER")
    return '"' + value + '"'


def _ref(table: str) -> str:
    return _q(LAB_SCHEMA) + "." + _q(table)


def _default(value: str | None) -> str:
    if value is None:
        return ""
    if _NUMBER.fullmatch(value) or _TEXT_LITERAL.fullmatch(value):
        return " DEFAULT " + value
    if value.upper() == "CURRENT_TIMESTAMP":
        # SQLite timestamp is ISO-like text, not a PostgreSQL timestamptz.
        return " DEFAULT to_char(current_timestamp AT TIME ZONE 'UTC', 'YYYY-MM-DD HH24:MI:SS')"
    raise ManifestError("V18_UNREVIEWED_DEFAULT")


def _checks(sql: str) -> list[str]:
    """Read CHECK expressions with a balanced, quote-aware SQL lexer.

    This is NOT a general SQL parser. CHECK expressions stay byte-for-byte
    unchanged except the explicitly reviewed BLOB length() exception.
    """
    results = []
    n = len(sql)
    i = 0

    def skip_quoted(index: int) -> int:
        marker = sql[index]
        index += 1
        while index < n:
            if sql[index] == marker:
                if index + 1 < n and sql[index + 1] == marker:
                    index += 2
                    continue
                return index + 1
            index += 1
        raise ManifestError("V18_UNTERMINATED_SQL_QUOTE")

    while i < n:
        if sql[i] in ("'", '"'):
            i = skip_quoted(i)
            continue
        if sql[i:i + 2] == "--":
            end = sql.find("\n", i + 2)
            i = n if end < 0 else end + 1
            continue
        if sql[i:i + 2] == "/*":
            end = sql.find("*/", i + 2)
            if end < 0:
                raise ManifestError("V18_UNTERMINATED_SQL_COMMENT")
            i = end + 2
            continue
        if (sql[i:i + 5].upper() == "CHECK"
                and (i == 0 or not (sql[i - 1].isalnum() or sql[i - 1] == "_"))
                and (i + 5 == n or not (sql[i + 5].isalnum() or sql[i + 5] == "_"))):
            j = i + 5
            while j < n and sql[j].isspace():
                j += 1
            if j >= n or sql[j] != "(":
                raise ManifestError("V18_CHECK_SYNTAX_CHANGED")
            opening = j
            depth = 1
            j += 1
            while j < n and depth:
                if sql[j] in ("'", '"'):
                    j = skip_quoted(j)
                    continue
                if sql[j] == "(":
                    depth += 1
                elif sql[j] == ")":
                    depth -= 1
                j += 1
            if depth != 0:
                raise ManifestError("V18_UNBALANCED_CHECK")
            expression = sql[opening + 1:j - 1].strip()
            if not expression or ";" in expression:
                raise ManifestError("V18_UNREVIEWED_CHECK")
            results.append(expression)
            i = j
            continue
        i += 1
    return results


def _pg_checks(table: str, source_sql: str) -> list[str]:
    expressions = _checks(source_sql)
    mapped = []
    for expression in expressions:
        if table == "avatar_asset_blobs" and expression.replace(" ", "").lower() == "length(body)>0":
            mapped.append('octet_length("body") > 0')
        elif re.search(r"\b(?:json_valid|strftime|julianday|instr|glob)\s*\(", expression, re.I):
            raise ManifestError("V18_UNREVIEWED_CHECK_FUNCTION")
        else:
            mapped.append(expression)
    return mapped


def _table_ddl(table: dict) -> str:
    if table["kind"] != "ordinary":
        raise ManifestError("V18_NON_RELATIONAL_TABLE")
    cols = table["columns"]
    ordered_pk = [c for c in sorted(cols, key=lambda c: c["pk_ordinal"])
                  if c["pk_ordinal"]]
    if len({c["name"] for c in cols}) != len(cols):
        raise ManifestError("V18_DUPLICATE_COLUMN")
    if sorted(c["pk_ordinal"] for c in ordered_pk) != list(range(1, len(ordered_pk) + 1)):
        raise ManifestError("V18_PRIMARY_KEY_GAP")
    is_identity = "AUTOINCREMENT" in table["sqlite_create_sql"].upper()
    if is_identity and not (len(ordered_pk) == 1 and ordered_pk[0]["sqlite_type"] == "INTEGER"):
        raise ManifestError("V18_UNREVIEWED_IDENTITY")

    defs = []
    for col in cols:
        sql = _q(col["name"]) + " " + col["postgres_type"]
        if is_identity and col["name"] == ordered_pk[0]["name"]:
            sql += " GENERATED BY DEFAULT AS IDENTITY"
        if col["not_null"] and not col["pk_ordinal"]:
            sql += " NOT NULL"
        sql += _default(col["default_sqlite"])
        defs.append(sql)

    if ordered_pk:
        defs.append("PRIMARY KEY (" + ", ".join(_q(c["name"]) for c in ordered_pk) + ")")
    for item in table["unique_constraints"]:
        if item["origin"] == "u":
            defs.append("UNIQUE (" + ", ".join(_q(name) for name in item["columns"]) + ")")
        elif item["origin"] not in ("pk", "c"):
            raise ManifestError("V18_UNREVIEWED_UNIQUE_CONSTRAINT")
    for check in _pg_checks(table["name"], table["sqlite_create_sql"]):
        defs.append("CHECK (" + check + ")")
    return "CREATE TABLE " + _ref(table["name"]) + " (\n    " + ",\n    ".join(defs) + "\n)"


def _fk_ddl(table: dict, foreign_key: dict, ordinal: int, known_tables: set[str]) -> str:
    parent = foreign_key["parent"]
    if parent not in known_tables:
        raise ManifestError("V18_MISSING_FK_PARENT")
    if foreign_key["match"].upper() not in ("NONE", "SIMPLE"):
        raise ManifestError("V18_UNREVIEWED_FK_MATCH")
    actions = {"NO ACTION", "RESTRICT", "CASCADE", "SET NULL", "SET DEFAULT"}
    update, delete = foreign_key["on_update"].upper(), foreign_key["on_delete"].upper()
    if update not in actions or delete not in actions:
        raise ManifestError("V18_UNREVIEWED_FK_ACTION")
    cols = ", ".join(_q(c) for c in foreign_key["columns"])
    targets = ", ".join(_q(c) for c in foreign_key["referenced_columns"])
    return (
        "ALTER TABLE " + _ref(table["name"])
        + " ADD CONSTRAINT " + _q(f"fk_{table['name']}_{ordinal}")
        + " FOREIGN KEY (" + cols + ") REFERENCES " + _ref(parent)
        + " (" + targets + ") ON UPDATE " + update + " ON DELETE " + delete
    )


def _index_ddl(conn, index: dict) -> str:
    name = index["name"]
    source = conn.execute(
        "SELECT tbl_name FROM sqlite_schema WHERE name=? AND type='index'",
        (name,),
    ).fetchone()
    if source is None:
        raise ManifestError("V18_INDEX_MISSING")
    table = source[0]
    info = conn.execute("PRAGMA index_list(" + _q(table) + ")").fetchall()
    match = next((row for row in info if row[1] == name), None)
    if match is None or match[4]:
        raise ManifestError("V18_UNREVIEWED_PARTIAL_INDEX")
    parts = []
    rows = conn.execute("PRAGMA index_xinfo(" + _q(name) + ")").fetchall()
    for seq, cid, col, desc, coll, key in rows:
        if not key:
            continue
        if cid < 0 or col is None or coll.upper() != "BINARY":
            raise ManifestError("V18_UNREVIEWED_INDEX_EXPRESSION_OR_COLLATION")
        parts.append((seq, _q(col) + (" DESC" if desc else "")))
    if not parts:
        raise ManifestError("V18_EMPTY_INDEX")
    unique = "UNIQUE " if match[2] else ""
    return (
        "CREATE " + unique + "INDEX " + _q(name) + " ON " + _ref(table)
        + " (" + ", ".join(col for _, col in sorted(parts)) + ")"
    )


def pg_ddl_statements() -> tuple[list[str], dict]:
    """Generate empty lab SQL only; fail if canonical v18 metadata drifts."""
    manifest = build_manifest()
    statements = ["CREATE SCHEMA " + _q(LAB_SCHEMA)]
    ordinary = [t for t in manifest["tables"] if t["kind"] == "ordinary"]
    known = {t["name"] for t in ordinary}
    for table in ordinary:
        statements.append(_table_ddl(table))
    for table in ordinary:
        for ordinal, fk in enumerate(table["foreign_keys"]):
            statements.append(_fk_ddl(table, fk, ordinal, known))
    with synthetic_v18_connection() as source:
        for index in manifest["indexes"]:
            statements.append(_index_ddl(source, index))
    # Explicit PostgreSQL replacement structure; unlike FTS5 virtual content,
    # the search document must be populated/rebuilt under a separate importer.
    statements.extend((
        """
        CREATE TABLE amora_pg_v18_lab.memory_search (
            memory_id TEXT PRIMARY KEY REFERENCES amora_pg_v18_lab.memory_records(id)
                ON DELETE CASCADE,
            content TEXT NOT NULL,
            tags TEXT NOT NULL
        )
        """,
        """
        CREATE INDEX amora_v18_memory_search_gin
        ON amora_pg_v18_lab.memory_search
        USING GIN (to_tsvector('simple'::regconfig, content || ' ' || tags))
        """,
    ))
    return statements, {
        "source_tables": len(manifest["tables"]),
        "postgres_source_tables": len(ordinary),
        "source_indexes": len(manifest["indexes"]),
        "foreign_keys": sum(len(t["foreign_keys"]) for t in ordinary),
        "source_checks": sum(len(_checks(t["sqlite_create_sql"])) for t in ordinary),
        "source_unique": sum(
            c["origin"] == "u" for t in ordinary for c in t["unique_constraints"]
        ),
        "postgres_search_replacement": True,
    }


def install_pg_v18_lab(conn) -> dict:
    """Create only an empty synthetic schema, never tables in public/Aiven."""
    ensure_local_lab(conn)
    statements, report = pg_ddl_statements()
    with conn.transaction():
        for statement in statements:
            conn.execute(statement)
    return report
