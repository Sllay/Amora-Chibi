"""Static audit gates for serving Core SQLite SQL during Aiven migration.

Does not connect to a database or read private backup files.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from amora.storage import postgres_runtime_sql_audit as audit


_ROOT = Path(__file__).resolve().parents[1]


def test_repository_inventory_covers_actual_serving_stores():
    summary = audit.report(_ROOT)
    files = summary["source_file_counts"]
    for path in (
        "core/amora/storage/database.py",
        "core/amora/memory/store.py",
        "core/amora/conversation/store.py",
        "core/amora/storage/backend.py",
    ):
        assert files[path] > 0, path
    assert summary["status"] == "incomplete"
    assert summary["serving_postgres_certified"] is False
    assert summary["production_backend_switched"] is False
    assert summary["sql_call_sites"] == len(summary["sites"])
    assert summary["sql_call_sites"] >= 40
    assert summary["classifications"]["sqlite_sensitive"] > 0
    assert summary["classifications"]["dynamic"] > 0
    # The original MemoryStore relies on real JSON1/FTS5 and bound qmarks.
    assert summary["feature_counts"]["sqlite_json1"] > 0
    assert summary["feature_counts"]["fts5_or_bm25"] > 0
    assert summary["feature_counts"]["qmark_placeholder"] > 0
    assert all("postgres_" not in name.split("/")[-1]
               for name in files)


def test_static_sql_features_and_dynamic_queries_are_not_silently_certified():
    source = '''
def example(conn, sql):
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("SELECT json_extract(metadata_json, '$.turn_id') FROM memory_records WHERE id=?", ("x",))
    conn.execute("SELECT id FROM memory_fts WHERE memory_fts MATCH ?", ("cafe",))
    conn.execute(sql)
    conn.execute("SELECT 1")
    conn.executemany("INSERT OR IGNORE INTO tags(id) VALUES (?)", [])
    execute_atomic_statement(conn, conn, "UPDATE memory_records SET content=? WHERE id=?", ())
    conn.executescript("CREATE TABLE items (id INTEGER)")
'''
    result = audit.scan_source(source, path="invented.py")
    assert len(result) == 8
    by_line = {item.line: item for item in result}
    assert "sqlite_pragma" in by_line[3].flags
    assert "sqlite_json1" in by_line[4].flags
    assert "qmark_placeholder" in by_line[4].flags
    assert "fts5_or_bm25" in by_line[5].flags
    assert by_line[6].classification == "dynamic"
    assert by_line[6].fingerprint is None
    assert by_line[7].classification == "unreviewed_sql"
    assert "sqlite_conflict" in by_line[8].flags
    assert by_line[9].method == "execute_atomic_statement"
    assert by_line[10].method == "executescript"


def test_known_module_constants_and_sql_concatenation_are_resolved():
    source = '''
QUERY = "SELECT id"
def find(conn):
    conn.execute(QUERY + " FROM events WHERE id=?", ("synthetic",))
    conn.execute(QUERY)
'''
    found = audit.scan_source(source, path="fixture.py")
    assert len(found) == 2
    assert found[0].classification == "sqlite_sensitive"
    assert found[1].classification == "unreviewed_sql"
    assert found[0].fingerprint != found[1].fingerprint


def test_secrets_and_sql_content_not_in_inventory_report():
    source = '''
def example(conn):
    conn.execute("SELECT * FROM events WHERE account_token='synthetic-secret-text'")
'''
    found = audit.scan_source(source, path="fixture.py")
    assert len(found) == 1
    blob = json.dumps([item.__dict__ for item in found])
    assert "synthetic-secret-text" not in blob
    assert "SELECT" not in blob
    assert len(found[0].fingerprint) == 16


def test_dynamic_unknown_helper_is_never_treated_as_portable():
    source = '''
def store(conn, dynamic_sql):
    execute_atomic_statement(conn, conn, dynamic_sql, ())
    conn.execute(f"SELECT * FROM {dynamic_sql}")
'''
    entries = audit.scan_source(source, path="dynamic.py")
    assert len(entries) == 2
    assert all(item.classification == "dynamic" for item in entries)
    assert all("unresolved_sql" in item.flags for item in entries)


def test_unsafe_source_cannot_be_counted_as_empty(tmp_path):
    (tmp_path / "core" / "amora").mkdir(parents=True)
    with pytest.raises(ValueError, match="AMORA_SQL_AUDIT_EMPTY"):
        audit.scan_repository(tmp_path)
    (tmp_path / "core" / "amora" / "bad.py").write_text(
        "def syntax error:", encoding="utf-8"
    )
    with pytest.raises(SyntaxError):
        audit.scan_repository(tmp_path)


def test_no_postgres_activation_while_core_dialect_unreviewed(monkeypatch):
    from amora.storage.backend import backend_from_environment

    with pytest.raises(RuntimeError, match="CORE_POSTGRES_SQL_DIALECT_NOT_CERTIFIED"):
        audit.require_postgres_serving_ready(audit.report(_ROOT))
    monkeypatch.setenv("AMORA_STORAGE_BACKEND", "postgres")
    with pytest.raises(ValueError, match="sqlite or libsql"):
        backend_from_environment()


def test_keyword_sql_sites_are_audited_not_hidden_as_dynamic():
    source = '''
def example(conn, sql):
    conn.execute(sql="SELECT id FROM memory_fts WHERE memory_fts MATCH ?")
    conn.executemany(sql="INSERT OR IGNORE INTO tags(id) VALUES (?)", values=[])
    conn.executescript(script="PRAGMA foreign_keys=ON")
    conn.execute(sql=sql)
    execute_atomic_statement(backend=conn, connection=conn, sql="UPDATE events SET id=?")
'''
    found = audit.scan_source(source, path="keyword.py")
    assert len(found) == 5
    assert "fts5_or_bm25" in found[0].flags
    assert "sqlite_conflict" in found[1].flags
    # executescript uses a different keyword name and must remain fail-closed.
    assert found[2].classification == "dynamic"
    assert found[3].classification == "dynamic"
    assert "qmark_placeholder" in found[4].flags
