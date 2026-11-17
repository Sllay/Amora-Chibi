"""Exact FTS5 vocabulary/BM25 scoring experiment in disposable PostgreSQL 18.

This laboratory extracts *synthetic* SQLite FTS5 unicode61 term occurrences
through fts5vocab(instance), then stores term frequencies in PostgreSQL and
uses the documented FTS5 BM25 formula. The runtime Core is NOT integrated.

No live backup, DSN, network setup, or production data is accepted. A real
importer would require further design for incremental transactional updates,
source versioning, size/performance, privacy, and source freshness.
"""
from __future__ import annotations

from contextlib import closing
import math
import sqlite3

from .postgres_fts_parity_lab import _search_terms
from .postgres_memory_lab import ensure_local_lab

_SCHEMA = "amora_pg_v18_lab"
_BM25_K1 = 1.2
_BM25_B = 0.75
_BATCH = 128


class BM25LabError(ValueError):
    pass


def _require_memory_fixture(source: sqlite3.Connection) -> None:
    if any(row[2] for row in source.execute("PRAGMA database_list")):
        raise BM25LabError("BM25_SOURCE_MUST_BE_IN_MEMORY")
    if source.execute(
        "SELECT 1 FROM sqlite_schema WHERE name='memory_fts' AND type='table'"
    ).fetchone() is None:
        raise BM25LabError("BM25_SOURCE_FTS5_MISSING")


def normalized_fts5_terms(query: str) -> list[str]:
    """Use the canonical unicode61 tokenizer, not Python NFKD guessing.

    The temporary index is memory-only, never persisted. Terms are deduped
    as in canonical MemoryStore._tokens() before OR ranking.
    """
    terms = _search_terms(query)
    if any(len(term) > 128 for term in terms):
        raise BM25LabError("BM25_QUERY_TOKEN_TOO_LONG")
    with closing(sqlite3.connect(":memory:")) as source:
        source.execute("""
            CREATE VIRTUAL TABLE query_fts USING fts5(
                content, tokenize='unicode61 remove_diacritics 2'
            )
        """)
        source.execute(
            "INSERT INTO query_fts(content) VALUES (?)", (" ".join(terms),)
        )
        source.execute(
            "CREATE VIRTUAL TABLE query_vocab USING fts5vocab(query_fts,'row')"
        )
        return [row[0] for row in source.execute(
            "SELECT term FROM query_vocab ORDER BY term"
        )]


def install_bm25_lab(conn) -> None:
    """Explicit PG lab tables. Never mutate the main Core schema."""
    ensure_local_lab(conn)
    with conn.transaction():
        if conn.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname=%s", (_SCHEMA,)
        ).fetchone() is None:
            raise BM25LabError("BM25_TARGET_SCHEMA_MISSING")
        conn.execute("""
            CREATE TABLE amora_pg_v18_lab.fts_doc_tokens (
                memory_id TEXT PRIMARY KEY
                    REFERENCES amora_pg_v18_lab.memory_records(id)
                    ON DELETE CASCADE,
                document_length BIGINT NOT NULL CHECK (document_length>=0)
            )
        """)
        conn.execute("""
            CREATE TABLE amora_pg_v18_lab.fts_term_postings (
                memory_id TEXT NOT NULL
                    REFERENCES amora_pg_v18_lab.fts_doc_tokens(memory_id)
                    ON DELETE CASCADE,
                term TEXT NOT NULL,
                frequency BIGINT NOT NULL CHECK (frequency>0),
                PRIMARY KEY(memory_id,term)
            )
        """)
        conn.execute("""
            CREATE INDEX fts_term_postings_lookup
                ON amora_pg_v18_lab.fts_term_postings(term,memory_id)
        """)


def import_synthetic_fts5_vocabulary(source: sqlite3.Connection, conn) -> dict:
    """Import exact token counts *only* from in-memory fixture to PG lab."""
    ensure_local_lab(conn)
    _require_memory_fixture(source)
    n_source = source.execute("SELECT COUNT(*) FROM memory_fts").fetchone()[0]
    if conn.execute("""
        SELECT 1 FROM amora_pg_v18_lab.fts_doc_tokens LIMIT 1
    """).fetchone() is not None:
        raise BM25LabError("BM25_TARGET_ALREADY_INDEXED")
    count_pg = conn.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.memory_search
    """).fetchone()[0]
    if count_pg != n_source:
        raise BM25LabError("BM25_SOURCE_TARGET_DOCUMENT_COUNT_MISMATCH")
    # The FTS vocab is a *temporary* read-only view on the in-memory
    # synthetic FTS5 index; do not mutate any logical source records.
    source.execute("""
        CREATE VIRTUAL TABLE temp.amora_bm25_vocab
          USING fts5vocab(memory_fts,'instance')
    """)
    doc_rows = source.execute("""
        SELECT f.memory_id, COUNT(v.term)
        FROM memory_fts AS f
        LEFT JOIN temp.amora_bm25_vocab AS v ON v.doc=f.rowid
        GROUP BY f.memory_id ORDER BY f.memory_id
    """)
    posting_rows = source.execute("""
        SELECT f.memory_id, v.term, COUNT(*)
        FROM temp.amora_bm25_vocab AS v
        JOIN memory_fts AS f ON f.rowid=v.doc
        GROUP BY f.memory_id,v.term ORDER BY f.memory_id,v.term
    """)
    docs, postings = 0, 0
    with conn.transaction():
        with conn.cursor() as cursor:
            while batch := doc_rows.fetchmany(_BATCH):
                cursor.executemany("""
                    INSERT INTO amora_pg_v18_lab.fts_doc_tokens(
                        memory_id,document_length
                    ) VALUES (%s,%s)
                """, batch)
                docs += len(batch)
            while batch := posting_rows.fetchmany(_BATCH):
                cursor.executemany("""
                    INSERT INTO amora_pg_v18_lab.fts_term_postings(
                        memory_id,term,frequency
                    ) VALUES (%s,%s,%s)
                """, batch)
                postings += len(batch)
        pg_doc_count = conn.execute("""
            SELECT COUNT(*) FROM amora_pg_v18_lab.fts_doc_tokens
        """).fetchone()[0]
        if docs != n_source or pg_doc_count != n_source:
            raise BM25LabError("BM25_DOCUMENT_COUNT_DRIFT")
        pg_doc_sum = conn.execute("""
            SELECT COALESCE(SUM(document_length),0)
              FROM amora_pg_v18_lab.fts_doc_tokens
        """).fetchone()[0]
        pg_tf_sum = conn.execute("""
            SELECT COALESCE(SUM(frequency),0)
              FROM amora_pg_v18_lab.fts_term_postings
        """).fetchone()[0]
        if pg_doc_sum != pg_tf_sum:
            raise BM25LabError("BM25_TOKEN_COUNT_DRIFT")
    return {
        "status": "ok",
        "synthetic": True,
        "documents": docs,
        "unique_document_terms": postings,
        "matching_token_counts": True,
        "production_backend_switched": False,
    }


def bm25_ranked_scores(
    conn, *, query: str, owner_id: str, now: str,
    remote_only: bool = False,
) -> list[tuple[str, float]]:
    """Compute original FTS5 BM25 formula from normalized postings.

    Corpus N and df include *all* indexed FTS5 documents, even ones filtered
    from the final view; that's what SQLite FTS5 bm25 does. Owner, privacy and
    archived policies filter rows BEFORE the final top-150 limit.
    """
    ensure_local_lab(conn)
    terms = normalized_fts5_terms(query)
    if not terms:
        return []
    rows = conn.execute("""
        WITH terms AS (
            SELECT unnest(%s::text[]) AS term
        ),
        corpus AS (
            SELECT COUNT(*)::float8 AS n,
                   AVG(document_length)::float8 AS avgdl
              FROM amora_pg_v18_lab.fts_doc_tokens
        ),
        term_df AS (
            SELECT t.term, COUNT(p.memory_id)::float8 AS df
              FROM terms t
              LEFT JOIN amora_pg_v18_lab.fts_term_postings p
                     ON p.term=t.term
             GROUP BY t.term
        ),
        scored AS (
            SELECT m.id,
                   SUM(
                       -GREATEST(
                           0.000001::float8,
                           LN((c.n - df.df + 0.5) / (df.df + 0.5))
                       ) *
                       (p.frequency::float8 * 2.2) /
                       (p.frequency::float8 + 1.2 *
                           (0.25 + 0.75 *
                              d.document_length::float8 / c.avgdl))
                   ) AS rank_score
              FROM amora_pg_v18_lab.fts_term_postings p
              JOIN term_df df ON df.term=p.term
              JOIN amora_pg_v18_lab.fts_doc_tokens d ON d.memory_id=p.memory_id
              JOIN amora_pg_v18_lab.memory_records m ON m.id=p.memory_id
              CROSS JOIN corpus c
             WHERE c.avgdl>0
               AND m.owner_entity_id=%s
               AND m.privacy_scope NOT IN ('AUTH','SECRET')
               AND m.archived_at IS NULL
               AND (m.valid_until IS NULL OR m.valid_until >= %s)
               AND (%s=FALSE OR m.remote_processing_allowed=1)
             GROUP BY m.id
        )
        SELECT id, rank_score FROM scored
        ORDER BY rank_score ASC, id
        LIMIT 150
    """, (terms, owner_id, now, bool(remote_only))).fetchall()
    return [(row[0], float(row[1])) for row in rows]


def numeric_parity_report(
    source: sqlite3.Connection, conn, *, query: str, owner_id: str,
    now: str, remote_only: bool = False,
) -> dict:
    """Never emit IDs or per-record BM25 scores: privacy-safe aggregates only."""
    _require_memory_fixture(source)
    from .postgres_fts_parity_lab import sqlite_ranked_fts_ids
    expected = sqlite_ranked_fts_ids(
        source, query=query, owner_id=owner_id, now=now,
        remote_only=remote_only,
    )
    actual = bm25_ranked_scores(
        conn, query=query, owner_id=owner_id, now=now,
        remote_only=remote_only,
    )
    actual_ids = [item[0] for item in actual]
    return {
        "status": "measured",
        "fts5_hits": len(expected),
        "postgres_hits": len(actual),
        "candidate_set_equal": set(expected) == set(actual_ids),
        "rank_order_equal": expected == actual_ids,
        # Do NOT certify production, even when numeric experiment matches.
        "bm25_production_parity_certified": False,
        "core_storage_switched": False,
    }
