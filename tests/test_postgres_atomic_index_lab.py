"""Real PostgreSQL 18 transactional index maintenance, synthetic-only fixtures."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import math
import sqlite3
from types import SimpleNamespace

import pytest

from amora.storage import postgres_atomic_index_lab as atomic
from amora.storage import postgres_fts_bm25_lab as bm25
from amora.storage import postgres_memory_lab as guard
from amora.storage import postgres_v18_lab_schema as schema
from amora.storage import postgres_v18_manifest as source_schema

import os

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Requires disposable local PostgreSQL 18 service",
)
NOW = "2026-11-17T00:00:00+00:00"
REV2 = "2026-11-17T00:01:00+00:00"


def connect():
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )


@pytest.fixture
def pair():
    pg = connect()
    sqlite = source_schema.synthetic_v18_connection()
    try:
        guard.ensure_local_lab(pg)
        pg.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        schema.install_pg_v18_lab(pg)
        bm25.install_bm25_lab(pg)
        yield sqlite, pg
    finally:
        sqlite.close()
        pg.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        pg.close()


def mirror(sqlite, mid, content, *, summary=None, tags=(), owner="BK",
           archived_at=None, action="insert", stamp=NOW):
    """Create the exact synthetic SQLite side of the same canonical FTS write."""
    searchable = " ".join(filter(None, (content, summary or "")))
    tag_text = " ".join(sorted(tags))
    if action == "insert":
        sqlite.execute("""
            INSERT INTO memory_records(
                id,kind,content,summary,provenance_kind,provenance_source,
                provenance_confidence,actor_id,privacy_scope,owner_entity_id,
                remote_processing_allowed,created_at,updated_at,
                importance,gate_score
            ) VALUES (?,'semantic',?,?,'user_assertion','synthetic:atomic-index',
                      1.0,?,'PERSONAL',?,0,?,?,0.8,0.8)
        """, (mid, content, summary, owner, owner, stamp, stamp))
    elif action == "update":
        sqlite.execute("""
            UPDATE memory_records SET content=?,summary=?,updated_at=? WHERE id=?
        """, (content, summary, stamp, mid))
        sqlite.execute("DELETE FROM memory_tags WHERE memory_id=?", (mid,))
        sqlite.execute("DELETE FROM memory_fts WHERE memory_id=?", (mid,))
    for tag in sorted(tags):
        sqlite.execute(
            "INSERT INTO memory_tags(memory_id,tag) VALUES (?,?)", (mid, tag)
        )
    sqlite.execute(
        "INSERT INTO memory_fts(memory_id,content,tags) VALUES (?,?,?)",
        (mid, searchable, tag_text),
    )
    sqlite.commit()


def matched_bm25(sqlite, pg, text):
    tokens = sorted(__import__("amora.memory.store", fromlist=["_tokens"])._tokens(text))[:16]
    expression = " OR ".join(f'"{t}"' for t in tokens)
    source_rows = sqlite.execute(
        """
        SELECT m.id, bm25(memory_fts)
          FROM memory_fts JOIN memory_records m ON m.id=memory_fts.memory_id
         WHERE memory_fts MATCH ?
           AND m.owner_entity_id='BK'
           AND m.privacy_scope NOT IN ('AUTH','SECRET')
           AND m.archived_at IS NULL
         ORDER BY bm25(memory_fts),m.id LIMIT 150
        """, (expression,),
    ).fetchall()
    pg_rows = bm25.bm25_ranked_scores(
        pg, query=text, owner_id="BK", now=NOW,
    )
    assert [r[0] for r in source_rows] == [r[0] for r in pg_rows]
    for left, right in zip(source_rows, pg_rows, strict=True):
        assert math.isclose(left[1], right[1], rel_tol=1e-9, abs_tol=1e-12)


def test_create_edit_tags_summary_and_bm25_consistency(pair):
    sqlite, pg = pair
    assert atomic.put_synthetic_memory(
        pg, memory_id="first", owner_id="BK", content="café coelho",
        summary="coração da floresta", tags=("mata", "amor"), revision=NOW,
    ) == "created"
    mirror(sqlite, "first", "café coelho", summary="coração da floresta",
           tags=("mata", "amor"))
    assert atomic.put_synthetic_memory(
        pg, memory_id="second", owner_id="BK", content="coelho saltando",
        tags=("animal",), revision=NOW,
    ) == "created"
    mirror(sqlite, "second", "coelho saltando", tags=("animal",))
    for phrase in ("coelho", "cafe", "coração", "mata"):
        matched_bm25(sqlite, pg, phrase)
    assert atomic.put_synthetic_memory(
        pg, memory_id="first", owner_id="BK", content="ação do coelho",
        summary="maçã verde", tags=("floresta",), revision=REV2,
        expected_revision=NOW,
    ) == "updated"
    mirror(sqlite, "first", "ação do coelho", summary="maçã verde",
           tags=("floresta",), action="update", stamp=REV2)
    assert pg.execute("""
        SELECT tags FROM amora_pg_v18_lab.memory_search WHERE memory_id='first'
    """).fetchone() == ("floresta",)
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.fts_term_postings
        WHERE memory_id='first' AND term='cafe'
    """).fetchone() == (0,)
    for phrase in ("coelho", "acao", "maçã", "cafe", "floresta"):
        matched_bm25(sqlite, pg, phrase)


def test_optimistic_revision_and_insert_only_prevent_overwrite(pair):
    _, pg = pair
    atomic.put_synthetic_memory(
        pg, memory_id="owned", owner_id="BK", content="original",
        revision=NOW,
    )
    with pytest.raises(atomic.SyntheticIndexError, match="LAB_MEMORY_ALREADY_EXISTS"):
        atomic.put_synthetic_memory(
            pg, memory_id="owned", owner_id="BK",
            content="unwanted duplicate", revision=REV2,
        )
    with pytest.raises(atomic.SyntheticIndexError, match="LAB_OWNER_MISMATCH"):
        atomic.put_synthetic_memory(
            pg, memory_id="owned", owner_id="OTHER",
            content="stolen", revision=REV2, expected_revision=NOW,
        )
    with pytest.raises(atomic.SyntheticIndexError, match="LAB_STALE_REVISION"):
        atomic.put_synthetic_memory(
            pg, memory_id="owned", owner_id="BK",
            content="stale", revision=REV2,
            expected_revision="2025-01-01T00:00:00+00:00",
        )
    assert pg.execute("""
        SELECT content FROM amora_pg_v18_lab.memory_records WHERE id='owned'
    """).fetchone() == ("original",)
    assert pg.execute("""
        SELECT content FROM amora_pg_v18_lab.memory_search WHERE memory_id='owned'
    """).fetchone() == ("original",)


def test_posting_constraint_failure_rolls_back_record_tags_and_search(pair):
    _, pg = pair
    atomic.put_synthetic_memory(
        pg, memory_id="safe", owner_id="BK", content="coelho",
        tags=("a",), revision=NOW,
    )
    pg.execute("""
        ALTER TABLE amora_pg_v18_lab.fts_term_postings
        ADD CONSTRAINT reject_bloqueado CHECK(term<>'bloqueado')
    """)
    psycopg = pytest.importorskip("psycopg")
    with pytest.raises(psycopg.errors.CheckViolation):
        atomic.put_synthetic_memory(
            pg, memory_id="safe", owner_id="BK", content="bloqueado",
            tags=("new",), expected_revision=NOW, revision=REV2,
        )
    assert pg.execute("""
        SELECT content,updated_at FROM amora_pg_v18_lab.memory_records
        WHERE id='safe'
    """).fetchone() == ("coelho", NOW)
    assert pg.execute("""
        SELECT content,tags FROM amora_pg_v18_lab.memory_search
        WHERE memory_id='safe'
    """).fetchone() == ("coelho", "a")
    assert pg.execute("""
        SELECT tag FROM amora_pg_v18_lab.memory_tags WHERE memory_id='safe'
    """).fetchall() == [("a",)]
    assert pg.execute("""
        SELECT term FROM amora_pg_v18_lab.fts_term_postings WHERE memory_id='safe'
    ORDER BY term
    """).fetchall() == [("coelho",)]


def test_archive_retains_corpus_then_hard_delete_cascades(pair):
    sqlite, pg = pair
    for mid, text in (("one", "café coelho"), ("two", "coelho")):
        atomic.put_synthetic_memory(
            pg, memory_id=mid, owner_id="BK", content=text,
            tags=("bicho",), revision=NOW,
        )
        mirror(sqlite, mid, text, tags=("bicho",))
    matched_bm25(sqlite, pg, "coelho")
    atomic.archive_synthetic_memory(
        pg, memory_id="one", owner_id="BK",
        expected_revision=NOW, revision=REV2,
    )
    sqlite.execute(
        "UPDATE memory_records SET archived_at=?,updated_at=? WHERE id='one'",
        (REV2, REV2),
    )
    sqlite.commit()
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.fts_doc_tokens
    """).fetchone() == (2,)
    matched_bm25(sqlite, pg, "coelho")
    atomic.hard_delete_synthetic_memory(
        pg, memory_id="one", owner_id="BK", expected_revision=REV2,
    )
    sqlite.execute("DELETE FROM memory_fts WHERE memory_id='one'")
    sqlite.execute("DELETE FROM memory_records WHERE id='one'")
    sqlite.commit()
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.fts_doc_tokens
    """).fetchone() == (1,)
    matched_bm25(sqlite, pg, "coelho")


def test_concurrent_edits_only_one_revision_can_commit(pair):
    _, pg = pair
    atomic.put_synthetic_memory(
        pg, memory_id="concurrent", owner_id="BK",
        content="before", revision=NOW,
    )

    def edit(ix):
        with connect() as connection:
            try:
                atomic.put_synthetic_memory(
                    connection, memory_id="concurrent", owner_id="BK",
                    content=f"after-{ix}", revision=f"{REV2}-{ix}",
                    expected_revision=NOW,
                )
                return "committed"
            except atomic.SyntheticIndexError as exc:
                return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(edit, (1, 2)))
    assert sorted(outcomes) == ["LAB_STALE_REVISION", "committed"]
    assert pg.execute("""
        SELECT m.content,m.updated_at,s.content
        FROM amora_pg_v18_lab.memory_records m
        JOIN amora_pg_v18_lab.memory_search s ON s.memory_id=m.id
        WHERE m.id='concurrent'
    """).fetchone()[0] in ("after-1", "after-2")
    row = pg.execute("""
        SELECT m.content,m.updated_at,s.content
        FROM amora_pg_v18_lab.memory_records m
        JOIN amora_pg_v18_lab.memory_search s ON s.memory_id=m.id
        WHERE m.id='concurrent'
    """).fetchone()
    assert row[0] == row[2]


def test_restart_keeps_committed_search_and_postings(pair):
    _, pg = pair
    atomic.put_synthetic_memory(
        pg, memory_id="restart", owner_id="BK",
        content="coração", tags=("coelho",), revision=NOW,
    )
    with connect() as fresh:
        rows = bm25.bm25_ranked_scores(
            fresh, query="coracao", owner_id="BK", now=NOW
        )
        assert [ident for ident, _ in rows] == ["restart"]
        assert fresh.execute("""
            SELECT COUNT(*) FROM amora_pg_v18_lab.fts_term_postings
            WHERE memory_id='restart'
        """).fetchone() == (2,)


def test_remote_connection_rejected_before_any_sql():
    class Remote:
        info = SimpleNamespace(
            host="some.aivencloud.com", dbname="amora_pg_synthetic",
            server_version=180002,
        )
        def execute(self, *_a, **_kw):
            raise AssertionError("Must not run external SQL")
        def transaction(self):
            raise AssertionError("Must not start external tx")
    with pytest.raises(ValueError, match="POSTGRES_LAB_LOCAL_DB_REQUIRED"):
        atomic.put_synthetic_memory(
            Remote(), memory_id="nope", owner_id="BK",
            content="test", revision=NOW,
        )
