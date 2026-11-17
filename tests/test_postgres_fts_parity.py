"""FTS5 unicode61/bm25 versus PG18 simple/ts_rank: synthetic parity gate."""
from contextlib import closing
import os

import pytest

from amora.storage import postgres_fts_parity_lab as fts
from amora.storage import postgres_memory_lab as guard
from amora.storage import postgres_synthetic_import as importer
from amora.storage import postgres_v18_lab_schema as pg_schema
from amora.storage import postgres_v18_manifest as source_schema

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Only disposable PostgreSQL 18 CI lab",
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


def record(source, memory_id, content, *, owner="BK", scope="PERSONAL",
           remote=1, archived=None):
    source.execute(
        """
        INSERT INTO memory_records (
           id,kind,content,provenance_kind,provenance_source,
           provenance_confidence,actor_id,privacy_scope,owner_entity_id,
           remote_processing_allowed,created_at,updated_at,
           importance,gate_score,archived_at
        ) VALUES (
           ?,'semantic',?,'user_assertion','synthetic:fts',
           1.0,?,?,?,? ,?,?,0.8,0.7,?
        )
        """,
        (
            memory_id,content,owner,scope,owner,remote,NOW,NOW,archived,
        ),
    )
    source.execute(
        """
        INSERT INTO memory_fts(memory_id,content,tags)
        VALUES (?,?,'')
        """, (memory_id,content),
    )


def test_ascii_fts_candidate_membership_parity_without_claiming_rank(pair):
    sqlite, pg = pair
    record(sqlite, "a","rabbit forest")
    record(sqlite, "b","forest rabbit rabbit")
    record(sqlite, "c","mountain tree")
    record(sqlite, "other","rabbit forest",owner="OTHER")
    record(sqlite, "auth","rabbit forest",scope="AUTH")
    record(sqlite, "secret","rabbit forest",scope="SECRET")
    record(sqlite, "archived","rabbit forest",archived=NOW)
    record(sqlite, "offline","rabbit forest",remote=0)
    sqlite.commit()
    importer.import_synthetic_v18(sqlite,pg)
    report=fts.parity_report(
        sqlite,pg,query="rabbit",owner_id="BK",now=NOW,remote_only=True
    )
    assert report["status"]=="measured"
    assert report["candidate_set_equal"] is True
    assert report["sqlite_hits"] == report["postgres_hits"] == 2
    assert report["fts5_bm25_parity_certified"] is False
    assert report["production_backend_switched"] is False
    # The Core's RRF/MMR and FTS lexical expansion remain unverified.
    with pytest.raises(fts.SearchParityError,match="FTS5_BM25_PARITY_NOT_CERTIFIED"):
        fts.require_certified_ranking()


def test_diacritic_difference_must_be_measured_not_ignored(pair):
    sqlite, pg = pair
    record(sqlite, "cafe-accent", "café com leite")
    record(sqlite, "cafe-plain", "cafe com leite")
    sqlite.commit()
    importer.import_synthetic_v18(sqlite,pg)
    report=fts.parity_report(sqlite,pg,query="cafe",owner_id="BK",now=NOW)
    assert report["candidate_set_equal"] is False
    assert report["sqlite_hits"] == 2
    assert report["postgres_hits"] == 1
    assert report["fts5_bm25_parity_certified"] is False


def test_unreviewed_query_chars_are_rejected_before_sql(pair):
    sqlite, pg = pair
    with pytest.raises(fts.SearchParityError,match="PG_FTS_NON_ASCII_QUERY_UNCERTIFIED"):
        fts.postgres_ranked_fts_ids(
            pg,query="café",owner_id="BK",now=NOW
        )
    with pytest.raises(fts.SearchParityError,match="FTS_QUERY_EMPTY"):
        fts.postgres_ranked_fts_ids(
            pg,query="!?",owner_id="BK",now=NOW
        )


def test_fts_report_contains_only_metrics_not_record_ids(pair):
    sqlite, pg = pair
    record(sqlite, "private-id-never-in-log","rabbit")
    sqlite.commit()
    importer.import_synthetic_v18(sqlite,pg)
    report=fts.parity_report(sqlite,pg,query="rabbit",owner_id="BK",now=NOW)
    assert "private-id-never-in-log" not in str(report)
    assert set(report)=={
        "status","sqlite_hits","postgres_hits","candidate_set_equal",
        "rank_order_equal","fts5_bm25_parity_certified",
        "production_backend_switched",
    }
