"""FTS5 vs PostgreSQL 18 search *comparison*, not a production adapter.

Only synthetic SQLite in-memory fixtures and disposable loopback PostgreSQL
amora_pg_synthetic may be used. PostgreSQL 'simple'/ts_rank is NOT an
equivalent substitute for SQLite FTS5 unicode61/remove_diacritics/bm25.
This lab detects mismatches and never certifies the ranking itself.
"""
from __future__ import annotations

import re
import sqlite3

from amora.memory.store import _tokens
from .postgres_memory_lab import ensure_local_lab

_ASCII_TERM = re.compile(r"[a-z0-9_]+\Z")
_MAX_TERMS = 16
_MAX_FTS = 150


class SearchParityError(ValueError):
    pass


def _search_terms(query: str) -> list[str]:
    terms = sorted(_tokens(query))[:_MAX_TERMS]
    if not terms:
        raise SearchParityError("FTS_QUERY_EMPTY")
    return terms


def sqlite_ranked_fts_ids(source: sqlite3.Connection, *, query: str,
                          owner_id: str, now: str,
                          remote_only: bool = False) -> list[str]:
    """Mirror canonical FTS5 bm25 ordering, with a synthetic owner filter.

    Candidate filters are applied BEFORE the 150-row FTS cap.
    """
    terms = _search_terms(query)
    expression = " OR ".join('"' + t.replace('"', '""') + '"' for t in terms)
    return [row[0] for row in source.execute(
        """
        SELECT m.id FROM memory_fts AS f
        JOIN memory_records AS m ON m.id=f.memory_id
        WHERE memory_fts MATCH ?
          AND m.owner_entity_id=?
          AND m.privacy_scope NOT IN ('AUTH','SECRET')
          AND m.archived_at IS NULL
          AND (m.valid_until IS NULL OR m.valid_until>=?)
          AND (?=0 OR m.remote_processing_allowed=1)
        ORDER BY bm25(memory_fts),m.id LIMIT 150
        """.replace("memory_fts AS f", "memory_fts"),
        (expression, owner_id, now, int(remote_only)),
    )]


def postgres_ranked_fts_ids(conn, *, query: str, owner_id: str, now: str,
                            remote_only: bool = False) -> list[str]:
    """Experimental PG counterpart; not a valid drop-in Core query."""
    ensure_local_lab(conn)
    terms = _search_terms(query)
    if any(not _ASCII_TERM.fullmatch(term) for term in terms):
        raise SearchParityError("PG_FTS_NON_ASCII_QUERY_UNCERTIFIED")
    # Strict ASCII identifier-like terms only; to_tsquery OR syntax is
    # constructed from validated terms, never raw user search text.
    expression = " | ".join(terms)
    return [row[0] for row in conn.execute(
        """
        SELECT m.id
        FROM amora_pg_v18_lab.memory_search AS s
        JOIN amora_pg_v18_lab.memory_records AS m ON m.id=s.memory_id
        WHERE m.owner_entity_id=%s
          AND m.privacy_scope NOT IN ('AUTH','SECRET')
          AND m.archived_at IS NULL
          AND (m.valid_until IS NULL OR m.valid_until>=%s)
          AND (%s=FALSE OR m.remote_processing_allowed=1)
          AND to_tsvector('simple'::regconfig,s.content || ' ' || s.tags)
              @@ to_tsquery('simple'::regconfig,%s)
        ORDER BY ts_rank(
            to_tsvector('simple'::regconfig,s.content || ' ' || s.tags),
            to_tsquery('simple'::regconfig,%s)
        ) DESC,m.id
        LIMIT 150
        """,
        (owner_id, now, bool(remote_only), expression, expression),
    )]


def parity_report(source: sqlite3.Connection, conn, *, query: str,
                  owner_id: str, now: str, remote_only: bool = False) -> dict:
    """Compare candidate membership and ranking without publishing record IDs.

    Equal results on some fixtures are NOT a certification. The adapter must
    still fail closed for FTS5 ranking until full parity is demonstrated.
    """
    left = sqlite_ranked_fts_ids(
        source, query=query, owner_id=owner_id, now=now,
        remote_only=remote_only,
    )
    right = postgres_ranked_fts_ids(
        conn, query=query, owner_id=owner_id, now=now,
        remote_only=remote_only,
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


def require_certified_ranking() -> None:
    """Prevent accidental promotion of PG simple/ts_rank as FTS5 parity."""
    raise SearchParityError("FTS5_BM25_PARITY_NOT_CERTIFIED")
