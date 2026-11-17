"""Real PostgreSQL 18 synthetic-only integration tests; never use Aiven DSN."""
from __future__ import annotations

import json
import os
import sqlite3

import pytest

from amora.storage import postgres_memory_lab as lab


pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Dedicated disposable loopback PostgreSQL 18 lab only",
)


@pytest.fixture
def pg():
    psycopg = pytest.importorskip("psycopg")
    connection = psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )
    try:
        lab.ensure_local_lab(connection)
        connection.execute("DROP SCHEMA IF EXISTS amora_pg_lab CASCADE")
        lab.install_lab(connection)
        yield connection
    finally:
        try:
            connection.execute("DROP SCHEMA IF EXISTS amora_pg_lab CASCADE")
        finally:
            connection.close()


def test_postgres_18_memory_graph_fk_cascade_and_uniqueness(pg):
    psycopg = pytest.importorskip("psycopg")
    lab.put_synthetic_memory(pg, memory_id="memory-one", owner="BK",
                             content="amora rabbit forest", tags=("rabbit", "forest"))
    lab.put_synthetic_memory(pg, memory_id="memory-two", owner="BK",
                             content="amora second memory", tags=("green",))
    pg.execute("""
        INSERT INTO amora_pg_lab.memory_entities(memory_id,entity_id,confidence)
        VALUES ('memory-one','entity-BK',0.9)
    """)
    pg.execute("""
        INSERT INTO amora_pg_lab.memory_links
              (source_memory_id,target_memory_id,link_type,confidence)
        VALUES ('memory-one','memory-two','related',0.7)
    """)
    with pytest.raises(psycopg.errors.UniqueViolation):
        lab.put_synthetic_memory(pg, memory_id="memory-one", owner="BK",
                                 content="duplicate should rollback", tags=("duplicate",))
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with pg.transaction():
            pg.execute("""
                INSERT INTO amora_pg_lab.memory_entities(memory_id,entity_id,confidence)
                VALUES ('missing','entity-X',0.5)
            """)
    assert pg.execute("SELECT COUNT(*) FROM amora_pg_lab.memory_records").fetchone()[0] == 2
    assert pg.execute("SELECT COUNT(*) FROM amora_pg_lab.memory_search").fetchone()[0] == 2
    assert pg.execute("SELECT COUNT(*) FROM amora_pg_lab.memory_tags WHERE tag='duplicate'").fetchone()[0] == 0

    pg.execute("DELETE FROM amora_pg_lab.memory_records WHERE id='memory-one'")
    for table in ("memory_entities", "memory_tags", "memory_links", "memory_search"):
        assert pg.execute(f"SELECT COUNT(*) FROM amora_pg_lab.{table}").fetchone()[0] == (
            1 if table in ("memory_tags", "memory_search") else 0
        )


def test_postgres_remote_scope_archive_and_text_candidates(pg):
    lab.put_synthetic_memory(pg, memory_id="owner-allowed", owner="BK",
                             scope="PERSONAL", content="purple rabbits live in forests",
                             tags=("rabbit",), remote_allowed=True)
    lab.put_synthetic_memory(pg, memory_id="owner-local-only", owner="BK",
                             scope="PRIVATE", content="private rabbit record",
                             remote_allowed=False)
    lab.put_synthetic_memory(pg, memory_id="owner-auth", owner="BK",
                             scope="AUTH", content="rabbit secret", remote_allowed=True)
    lab.put_synthetic_memory(pg, memory_id="owner-secret", owner="BK",
                             scope="SECRET", content="rabbit secret", remote_allowed=True)
    lab.put_synthetic_memory(pg, memory_id="other-owner", owner="OTHER",
                             scope="PUBLIC", content="rabbit outside owner",
                             remote_allowed=True)
    lab.put_synthetic_memory(pg, memory_id="archived", owner="BK",
                             scope="PUBLIC", content="archived rabbit",
                             archived_at="2026-01-02T00:00:00+00:00", remote_allowed=True)

    assert lab.candidate_ids(pg, owner="BK", query="rabbit", remote_only=True) == [
        "owner-allowed"
    ]
    assert set(lab.candidate_ids(pg, owner="BK", query="rabbit")) == {
        "owner-allowed", "owner-local-only"
    }
    assert lab.candidate_ids(pg, owner="OTHER", query="rabbit") == ["other-owner"]
    assert lab.candidate_ids(pg, owner="BK", query="noneofthesewords") == []


def test_postgres_json_cast_rollback_and_gin_index(pg):
    psycopg = pytest.importorskip("psycopg")
    metadata = json.dumps({"turn_id": "turn-synthetic", "explicit_memory_request": True})
    lab.put_synthetic_memory(
        pg, memory_id="json-one", owner="BK", content="a synthetic meadow",
        metadata_json=metadata,
    )
    rows = pg.execute("""
        SELECT id FROM amora_pg_lab.memory_records
         WHERE metadata_json::jsonb ->> 'turn_id' = 'turn-synthetic'
           AND (metadata_json::jsonb ->> 'explicit_memory_request')::boolean = TRUE
    """).fetchall()
    assert rows == [("json-one",)]
    idx = pg.execute("""
        SELECT indexdef FROM pg_indexes
         WHERE schemaname='amora_pg_lab' AND indexname='lab_memory_search_gin'
    """).fetchone()
    assert idx is not None and "USING gin" in idx[0]

    with pytest.raises(psycopg.errors.CheckViolation):
        with pg.transaction():
            lab.put_synthetic_memory(
                pg, memory_id="invalid-one", owner="BK", content="invalid",
                scope="PRIVATE", remote_allowed=True,
            )
            pg.execute("""
                UPDATE amora_pg_lab.memory_records
                   SET remote_processing_allowed=2 WHERE id='invalid-one'
            """)
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_lab.memory_records WHERE id='invalid-one'
    """).fetchone()[0] == 0
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_lab.memory_search WHERE memory_id='invalid-one'
    """).fetchone()[0] == 0


def test_memory_columns_match_canonical_v18_sqlite_migrations(pg):
    """Five lab tables must not silently omit fields from canonical v18."""
    from amora.storage.migrations import MIGRATIONS

    tables = (
        "events", "memory_records", "memory_entities",
        "memory_tags", "memory_links",
    )
    with sqlite3.connect(":memory:") as source:
        for version, ddl in MIGRATIONS:
            if version > 3:
                break
            source.executescript(ddl)
        for table in tables:
            sqlite_fields = {
                row[1] for row in source.execute(f"PRAGMA table_info('{table}')")
            }
            pg_fields = {
                row[0] for row in pg.execute(
                    """
                    SELECT column_name FROM information_schema.columns
                     WHERE table_schema='amora_pg_lab' AND table_name=%s
                    """,
                    (table,),
                ).fetchall()
            }
            assert sqlite_fields, table
            assert pg_fields == sqlite_fields, (
                f"PostgreSQL lab columns diverge from canonical SQLite table {table}"
            )
