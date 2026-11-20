"""Full-text accent-folding comparison against real PG18 and SQLite FTS5.

Only invented text and a disposable PostgreSQL lab. Rank parity is NOT assumed.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from amora.storage import postgres_fts_folded_lab as folded
from amora.storage import postgres_fts_parity_lab as plain
from amora.storage import postgres_memory_lab as guard
from amora.storage import postgres_synthetic_import as importer
from amora.storage import postgres_v18_lab_schema as pg_schema
from amora.storage import postgres_v18_manifest as source_schema

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Requires disposable PG18 loopback CI service",
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


def record(source, memory_id, content, *, owner="BK",
           privacy="PERSONAL", remote=1, archived=None):
    source.execute(
        """
        INSERT INTO memory_records (
          id,kind,content,provenance_kind,provenance_source,
          provenance_confidence,actor_id,privacy_scope,owner_entity_id,
          remote_processing_allowed,created_at,updated_at,
          importance,gate_score,archived_at
        ) VALUES (
          ?,'semantic',?,'user_assertion','synthetic:folded',
          1.0,?,?,?,?,?,?,0.8,0.7,?
        )
        """,
        (memory_id, content, owner, privacy, owner, remote, NOW, NOW, archived),
    )
    source.execute(
        "INSERT INTO memory_fts(memory_id,content,tags) VALUES (?,?,'')",
        (memory_id, content),
    )


def test_unaccent_fixes_multiple_portuguese_diacritic_candidate_sets(pair):
    sqlite, pg = pair
    pairs = [
        ("cafe", "café"),
        ("acao", "ação"),
        ("maca", "maçã"),
        ("sao", "São"),
        ("coracao", "coração"),
        ("jupiter", "Júpiter"),
    ]
    for index, (plain_word, accented) in enumerate(pairs):
        record(sqlite, f"p{index}", plain_word)
        record(sqlite, f"a{index}", accented)
    sqlite.commit()
    importer.import_synthetic_v18(sqlite, pg)
    folded.install_folded_search(pg)

    for plain_word, accented in pairs:
        result = folded.folded_parity_report(
            sqlite, pg, query=plain_word, owner_id="BK", now=NOW,
        )
        assert result["candidate_set_equal"] is True, (plain_word, accented, result)
        assert result["sqlite_hits"] == result["postgres_hits"] == 2
        assert result["fts5_bm25_parity_certified"] is False
        reverse = folded.folded_parity_report(
            sqlite, pg, query=accented, owner_id="BK", now=NOW,
        )
        assert reverse["candidate_set_equal"] is True, (plain_word, accented, reverse)
        assert reverse["postgres_hits"] == 2

    # The old PG 'simple' index is deliberately NOT equivalent to FTS5.
    baseline = plain.parity_report(
        sqlite, pg, query="cafe", owner_id="BK", now=NOW,
    )
    assert baseline["candidate_set_equal"] is False


def test_filters_applied_before_fts_cap_and_no_disclosure(pair):
    sqlite, pg = pair
    record(sqlite, "allowed-a", "café bosque")
    record(sqlite, "allowed-b", "cafe bosque", remote=1)
    record(sqlite, "wrong-owner", "café bosque", owner="OTHER")
    record(sqlite, "auth", "café bosque", privacy="AUTH")
    record(sqlite, "secret", "café bosque", privacy="SECRET")
    record(sqlite, "archived", "café bosque", archived=NOW)
    record(sqlite, "local-only", "café bosque", remote=0)
    sqlite.commit()
    importer.import_synthetic_v18(sqlite, pg)
    folded.install_folded_search(pg)
    report = folded.folded_parity_report(
        sqlite, pg, query="cafe", owner_id="BK",
        now=NOW, remote_only=True,
    )
    assert report["sqlite_hits"] == report["postgres_hits"] == 2
    assert report["candidate_set_equal"] is True
    assert "allowed-a" not in str(report)
    assert "secret" not in str(report)
    with pytest.raises(plain.SearchParityError, match="FTS5_BM25_PARITY_NOT_CERTIFIED"):
        plain.require_certified_ranking()


def test_folded_index_and_dictionary_configuration(pair):
    sqlite, pg = pair
    record(sqlite, "doc", "coração da mata")
    sqlite.commit()
    importer.import_synthetic_v18(sqlite, pg)
    folded.install_folded_search(pg)
    assert pg.execute("""
        SELECT indexdef FROM pg_indexes
         WHERE schemaname='amora_pg_v18_lab'
           AND indexname='amora_v18_memory_search_folded_gin'
    """).fetchone()[0].lower().count("using gin") == 1
    assert pg.execute("""
        SELECT to_tsquery('amora_pg_v18_lab.amora_folded'::regconfig,'coracao')::text
    """).fetchone()[0] == "'coracao'"
    assert pg.execute("""
        SELECT to_tsquery('amora_pg_v18_lab.amora_folded'::regconfig,'coração')::text
    """).fetchone()[0] == "'coracao'"
    assert folded.folded_parity_report(
        sqlite, pg, query="coracao", owner_id="BK", now=NOW
    )["candidate_set_equal"]


def test_fail_closed_nonlocal_pg_connection_before_ddl():
    class Remote:
        info = SimpleNamespace(
            host="service.aivencloud.com", dbname="amora_pg_synthetic",
            server_version=180000,
        )
        def execute(self, *_a, **_kw):
            raise AssertionError("Aiven must never receive lab SQL")
        def transaction(self):
            raise AssertionError("Aiven must never begin lab transaction")
    with pytest.raises(ValueError, match="POSTGRES_LAB_LOCAL_DB_REQUIRED"):
        folded.install_folded_search(Remote())


def test_no_untrusted_tsquery_operators_or_leaked_ids(pair):
    sqlite, pg = pair
    record(sqlite, "memory-id-only-in-db", "São coelhos")
    sqlite.commit()
    importer.import_synthetic_v18(sqlite, pg)
    folded.install_folded_search(pg)
    report = folded.folded_parity_report(
        sqlite, pg, query="sao OR coelhos", owner_id="BK", now=NOW
    )
    assert report["candidate_set_equal"] is True
    assert "memory-id-only-in-db" not in str(report)
    assert folded._expression("café:*) | amor") == "amor | café"
    with pytest.raises(folded.FoldedSearchError,
                       match="FOLDED_QUERY_TOKEN_UNSUPPORTED"):
        folded._expression("supercalifragilisticexpialidocious" * 4)
