"""Exact FTS5 BM25 numeric comparison against a disposable PostgreSQL 18.

Postings come from SQLite's own unicode61 FTS5 vocabulary (not an approximate
Python regex). Tests use only invented text; no PCS4C or live Aiven records.
"""
from __future__ import annotations

import math
import os
from types import SimpleNamespace

import pytest

from amora.memory.store import MemoryStore
from amora.storage import postgres_fts_bm25_lab as bm25
from amora.storage import postgres_fts_parity_lab as baseline
from amora.storage import postgres_memory_lab as guard
from amora.storage import postgres_synthetic_import as importer
from amora.storage import postgres_v18_lab_schema as pg_schema
from amora.storage import postgres_v18_manifest as source_schema

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Dedicated disposable PostgreSQL 18 local lab only",
)
NOW = "2026-11-17T00:00:00+00:00"


@pytest.fixture
def pair():
    psycopg = pytest.importorskip("psycopg")
    pg = psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )
    source = source_schema.synthetic_v18_connection()
    try:
        guard.ensure_local_lab(pg)
        pg.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        pg_schema.install_pg_v18_lab(pg)
        yield source, pg
    finally:
        source.close()
        try:
            pg.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        finally:
            pg.close()


def memory(source, ident, content, *, tags="", owner="BK", scope="PERSONAL",
           remote=1, archived=None):
    source.execute(
        """
        INSERT INTO memory_records(
          id,kind,content,provenance_kind,provenance_source,
          provenance_confidence,actor_id,privacy_scope,owner_entity_id,
          remote_processing_allowed,created_at,updated_at,
          importance,gate_score,archived_at
        ) VALUES (
          ?,'semantic',?,'user_assertion','synthetic:bm25',
          1.0,?,?,?,?,?,?,0.8,0.7,?
        )
        """,
        (ident, content, owner, scope, owner, remote, NOW, NOW, archived),
    )
    source.execute(
        "INSERT INTO memory_fts(memory_id,content,tags) VALUES (?,?,?)",
        (ident, content, tags),
    )


def canonical_sqlite_scores(source, *, query, owner="BK", remote_only=False):
    terms = baseline._search_terms(query)
    expr = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    return source.execute(
        """
        SELECT m.id, bm25(memory_fts)
          FROM memory_fts
          JOIN memory_records m ON m.id=memory_fts.memory_id
         WHERE memory_fts MATCH ?
           AND m.owner_entity_id=?
           AND m.privacy_scope NOT IN ('AUTH','SECRET')
           AND m.archived_at IS NULL
           AND (m.valid_until IS NULL OR m.valid_until>=?)
           AND (?=0 OR m.remote_processing_allowed=1)
         ORDER BY bm25(memory_fts),m.id LIMIT 150
        """,
        (expr, owner, NOW, int(remote_only)),
    ).fetchall()


def _certify_numeric(source, pg, query, *, owner="BK", remote_only=False):
    expected = canonical_sqlite_scores(
        source, query=query, owner=owner, remote_only=remote_only
    )
    actual = bm25.bm25_ranked_scores(
        pg, query=query, owner_id=owner, now=NOW, remote_only=remote_only
    )
    assert [r[0] for r in actual] == [r[0] for r in expected], query
    for (original_id, original_score), (pg_id, pg_score) in zip(
        expected, actual, strict=True
    ):
        assert original_id == pg_id
        assert math.isclose(
            original_score, pg_score, rel_tol=1e-9, abs_tol=1e-12
        ), (query, original_score, pg_score)
    report = bm25.numeric_parity_report(
        source, pg, query=query, owner_id=owner, now=NOW,
        remote_only=remote_only,
    )
    assert report["candidate_set_equal"] is True
    assert report["rank_order_equal"] is True
    assert report["bm25_production_parity_certified"] is False
    assert report["core_storage_switched"] is False


def test_token_frequency_idf_bm25_and_portuguese_accents_match_sqlite(pair):
    sqlite, pg = pair
    memory(sqlite, "a", "café coelho coelho", tags="floresta")
    memory(sqlite, "b", "cafe cafe coelho", tags="animal coelho")
    memory(sqlite, "c", "coração apaixonado", tags="coelho amor")
    memory(sqlite, "d", "coração coração", tags="sentimentos")
    memory(sqlite, "e", "animal na mata", tags="animal")
    memory(sqlite, "f", "só uma árvore", tags="sem coelho")
    memory(sqlite, "g", "café secreto", scope="SECRET")
    memory(sqlite, "h", "café outro", owner="OTHER")
    memory(sqlite, "i", "café arquivado", archived=NOW)
    memory(sqlite, "j", "café local", remote=0)
    sqlite.commit()
    importer.import_synthetic_v18(sqlite, pg)
    bm25.install_bm25_lab(pg)
    report = bm25.import_synthetic_fts5_vocabulary(sqlite, pg)
    assert report["documents"] == 10
    assert report["matching_token_counts"] is True
    assert report["synthetic"] is True
    for query in ("cafe", "café", "coelho", "coração", "coracao", "animal",
                  "cafe coelho", "coelho coração", "floresta", "absente"):
        _certify_numeric(sqlite, pg, query)
    _certify_numeric(sqlite, pg, "café", remote_only=True)
    _certify_numeric(sqlite, pg, "café", owner="OTHER")


def test_source_vocab_is_precise_and_idf_uses_global_corpus(pair):
    sqlite, pg = pair
    memory(sqlite, "a", "café café cafe")
    memory(sqlite, "b", "cafe")
    memory(sqlite, "other", "café", owner="OTHER")
    memory(sqlite, "secret", "café", scope="SECRET")
    sqlite.commit()
    importer.import_synthetic_v18(sqlite, pg)
    bm25.install_bm25_lab(pg)
    bm25.import_synthetic_fts5_vocabulary(sqlite, pg)
    assert bm25.normalized_fts5_terms("café") == ["cafe"]
    assert pg.execute("""
        SELECT frequency
        FROM amora_pg_v18_lab.fts_term_postings
        WHERE memory_id='a' AND term='cafe'
    """).fetchone() == (3,)
    assert pg.execute("""
        SELECT document_length FROM amora_pg_v18_lab.fts_doc_tokens
        WHERE memory_id='a'
    """).fetchone() == (3,)
    _certify_numeric(sqlite, pg, "cafe")
    with pytest.raises(bm25.BM25LabError, match="BM25_TARGET_ALREADY_INDEXED"):
        bm25.import_synthetic_fts5_vocabulary(sqlite, pg)


def test_canonical_rrf_mmr_fusion_can_be_reused_on_matched_bm25_order(pair):
    sqlite, pg = pair
    memory(sqlite, "one", "café de manhã coelho")
    memory(sqlite, "two", "coelho na floresta")
    memory(sqlite, "three", "café coelho coração")
    sqlite.commit()
    importer.import_synthetic_v18(sqlite, pg)
    bm25.install_bm25_lab(pg)
    bm25.import_synthetic_fts5_vocabulary(sqlite, pg)
    source_order = [x[0] for x in canonical_sqlite_scores(
        sqlite, query="cafe coelho"
    )]
    dest_order = [x[0] for x in bm25.bm25_ranked_scores(
        pg, query="cafe coelho", owner_id="BK", now=NOW
    )]
    assert source_order == dest_order
    records = {
        row[0]: SimpleNamespace(content=row[1])
        for row in sqlite.execute("SELECT id,content FROM memory_records")
    }

    def fuse(text_order):
        scores = {key: 0.0 for key in records}
        reasons = {key: set() for key in records}
        MemoryStore._add_rrf(scores, reasons, text_order, 3.0, "TEXT_MATCH")
        MemoryStore._add_rrf(scores, reasons,
                             ["one", "three", "two"], 0.8, "RECENCY")
        MemoryStore._add_rrf(scores, reasons,
                             ["three", "one", "two"], 1.0,
                             "IMPORTANCE_RETRIEVABILITY")
        ranked = sorted(scores, key=scores.get, reverse=True)
        return MemoryStore._mmr_select(
            ranked, records, scores, limit=3, mmr_lambda=0.7
        )
    assert fuse(source_order) == fuse(dest_order)


def test_fail_closed_and_sanitized_stats_without_credentials(pair):
    sqlite, pg = pair
    memory(sqlite, "private-id-dont-print", "café na praia")
    sqlite.commit()
    importer.import_synthetic_v18(sqlite, pg)
    bm25.install_bm25_lab(pg)
    bm25.import_synthetic_fts5_vocabulary(sqlite, pg)
    report = bm25.numeric_parity_report(
        sqlite, pg, query="cafe", owner_id="BK", now=NOW
    )
    assert "private-id-dont-print" not in str(report)
    assert all(type(value) in (bool, str, int) for value in report.values())
    assert bm25.normalized_fts5_terms("áção") == ["acao"]


def test_no_remote_connection_even_for_ddl():
    class Remote:
        info = SimpleNamespace(
            host="amora.aivencloud.com", dbname="amora_pg_synthetic",
            server_version=180001
        )
        def execute(self, *_a, **_kw):
            raise AssertionError("no remote SQL allowed")
        def transaction(self):
            raise AssertionError("no remote transaction")
    with pytest.raises(ValueError, match="POSTGRES_LAB_LOCAL_DB_REQUIRED"):
        bm25.install_bm25_lab(Remote())
