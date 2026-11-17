"""Candidate FTS5-style diacritic folding with PostgreSQL 18 unaccent.

Laboratory only: requires a disposable loopback PostgreSQL 18 service and
an in-memory SQLite v18 fixture. Does NOT replace FTS5 bm25/RRF/MMR.
The extension exists in Aiven's supported list, but this module never
connects to Aiven or mutates real Core storage.
"""
from __future__ import annotations

import re
import sqlite3

from .postgres_fts_parity_lab import _search_terms, sqlite_ranked_fts_ids
from .postgres_memory_lab import ensure_local_lab

_CONFIG = "amora_pg_v18_lab.amora_folded"
_INDEX = "amora_v18_memory_search_folded_gin"
_TERM = re.compile(r"\w{2,80}\Z", flags=re.UNICODE)


class FoldedSearchError(ValueError):
    pass


def install_folded_search(conn) -> None:
    """Install an extra, explicit GIN-backed test configuration.

    This intentionally creates no search tables, copies no records, and does
    not modify the existing 'simple' comparison index. Reuses the isolated
    schema installed by postgres_v18_lab_schema.install_pg_v18_lab().
    """
    ensure_local_lab(conn)
    with conn.transaction():
        schema = conn.execute(
            "SELECT 1 FROM pg_namespace WHERE nspname='amora_pg_v18_lab'"
        ).fetchone()
        if schema is None:
            raise FoldedSearchError("FOLDED_LAB_SCHEMA_MISSING")
        # Only a synthetic lab is permitted. Install supported contrib
        # extension in the disposable database, never in Aiven here.
        conn.execute("CREATE EXTENSION IF NOT EXISTS unaccent WITH SCHEMA public")
        installed = conn.execute("""
            SELECT n.nspname FROM pg_extension e JOIN pg_namespace n
              ON n.oid=e.extnamespace WHERE e.extname='unaccent'
        """).fetchone()
        if installed != ("public",):
            raise FoldedSearchError("FOLDED_UNACCENT_SCHEMA_MISMATCH")
        conn.execute(
            "CREATE TEXT SEARCH CONFIGURATION amora_pg_v18_lab.amora_folded "
            "(COPY = pg_catalog.simple)"
        )
        conn.execute("""
            ALTER TEXT SEARCH CONFIGURATION amora_pg_v18_lab.amora_folded
              ALTER MAPPING FOR
                asciiword, word, hword, hword_part, asciihword, hword_asciipart
              WITH public.unaccent, pg_catalog.simple
        """)
        # Function and index config must match exactly. The existing
        # 'simple' index is not a semantic parity guarantee.
        conn.execute("""
            CREATE INDEX amora_v18_memory_search_folded_gin
              ON amora_pg_v18_lab.memory_search
              USING GIN (
                to_tsvector(
                  'amora_pg_v18_lab.amora_folded'::regconfig,
                  content || ' ' || tags
                )
              )
        """)


def _expression(query: str) -> str:
    terms = _search_terms(query)
    # Unicode words allowed; reject tsquery syntax, unbounded tokens and
    # quote/colon/operator injection before executing any query.
    if any(_TERM.fullmatch(term) is None for term in terms):
        raise FoldedSearchError("FOLDED_QUERY_TOKEN_UNSUPPORTED")
    return " | ".join(terms)


def folded_ranked_fts_ids(
    conn, *, query: str, owner_id: str, now: str,
    remote_only: bool = False,
) -> list[str]:
    """PostgreSQL GIN-backed candidates with owner filters before LIMIT.

    ts_rank is deliberately experimental; equal candidate sets do not
    certify equality of BM25 relevance or the final RRF/MMR ranking.
    """
    ensure_local_lab(conn)
    expression = _expression(query)
    rows = conn.execute("""
        SELECT m.id
          FROM amora_pg_v18_lab.memory_search AS s
          JOIN amora_pg_v18_lab.memory_records AS m ON m.id=s.memory_id
         WHERE m.owner_entity_id=%s
           AND m.privacy_scope NOT IN ('AUTH','SECRET')
           AND m.archived_at IS NULL
           AND (m.valid_until IS NULL OR m.valid_until>=%s)
           AND (%s=FALSE OR m.remote_processing_allowed=1)
           AND to_tsvector(
                 'amora_pg_v18_lab.amora_folded'::regconfig,
                 s.content || ' ' || s.tags
               ) @@ to_tsquery(
                 'amora_pg_v18_lab.amora_folded'::regconfig, %s
               )
         ORDER BY ts_rank(
               to_tsvector(
                 'amora_pg_v18_lab.amora_folded'::regconfig,
                 s.content || ' ' || s.tags
               ),
               to_tsquery(
                 'amora_pg_v18_lab.amora_folded'::regconfig, %s
               )
             ) DESC, m.id
         LIMIT 150
    """, (owner_id, now, bool(remote_only), expression, expression)).fetchall()
    return [row[0] for row in rows]


def folded_parity_report(
    source: sqlite3.Connection, conn, *, query: str,
    owner_id: str, now: str, remote_only: bool = False,
) -> dict[str, object]:
    """Safe aggregate parity metrics. Never return/print private row IDs."""
    left = sqlite_ranked_fts_ids(
        source, query=query, owner_id=owner_id,
        now=now, remote_only=remote_only,
    )
    right = folded_ranked_fts_ids(
        conn, query=query, owner_id=owner_id,
        now=now, remote_only=remote_only,
    )
    return {
        "status": "measured",
        "sqlite_hits": len(left),
        "postgres_hits": len(right),
        "candidate_set_equal": set(left) == set(right),
        "rank_order_equal": left == right,
        "fts5_bm25_parity_certified": False,
        "production_backend_switched": False,
    }
