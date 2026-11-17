"""Atomic synthetic memory/index mutation for disposable PostgreSQL 18.

No Aiven DSNs, no Core integration and no file-backed SQLite. The tokenizer
uses a fresh in-memory SQLite FTS5 index as a laboratory oracle; production
must separately certify its own tokenizer, transactional leases and costs.

Maintains memory_records, memory_tags, memory_search, fts_doc_tokens and
fts_term_postings as ONE PostgreSQL transaction. The optimistic revision
guards and per-memory SELECT FOR UPDATE prevent silent lost updates.
"""
from __future__ import annotations

from contextlib import closing
import sqlite3

from .postgres_memory_lab import ensure_local_lab

_LIMIT = 128 * 1024


class SyntheticIndexError(ValueError):
    """Fixed diagnostic codes: never interpolate content or memory IDs."""


def _check_identity(memory_id: str, owner: str | None = None) -> None:
    if not isinstance(memory_id, str) or not 0 < len(memory_id) <= 128:
        raise SyntheticIndexError("LAB_INVALID_ID")
    if owner is not None and (
        not isinstance(owner, str) or not 0 < len(owner) <= 128
    ):
        raise SyntheticIndexError("LAB_INVALID_OWNER")


def _document_terms(content: str, tags: str) -> tuple[int, list[tuple[str, int]]]:
    """Count exact FTS5 unicode61 terms, including content and tags fields.

    This reference tokenizer is SQLite in-memory only, never a PCS4C source.
    """
    if not isinstance(content, str) or not isinstance(tags, str):
        raise SyntheticIndexError("LAB_INVALID_TEXT")
    if len(content) + len(tags) > _LIMIT:
        raise SyntheticIndexError("LAB_DOCUMENT_TOO_LARGE")
    with closing(sqlite3.connect(":memory:")) as index:
        index.execute("""
            CREATE VIRTUAL TABLE synthetic_fts USING fts5(
                content,tags,tokenize='unicode61 remove_diacritics 2'
            )
        """)
        index.execute(
            "INSERT INTO synthetic_fts(content,tags) VALUES (?,?)",
            (content, tags),
        )
        index.execute("""
            CREATE VIRTUAL TABLE synthetic_vocab
            USING fts5vocab(synthetic_fts,'instance')
        """)
        items = [
            (term, int(tf)) for term, tf in index.execute("""
                SELECT term, COUNT(*) AS tf FROM synthetic_vocab
                GROUP BY term ORDER BY term
            """)
        ]
        return sum(n for _, n in items), items


def put_synthetic_memory(
    conn, *, memory_id: str, owner_id: str, content: str, revision: str,
    summary: str | None = None, tags: tuple[str, ...] = (),
    privacy_scope: str = "PERSONAL", remote_allowed: bool = False,
    expected_revision: str | None = None,
) -> str:
    """Insert/create or compare-and-swap edit; never implicit UPSERT fallback.

    - expected_revision=None means INSERT ONLY, refuses an existing memory
    - otherwise requires exact previous updated_at and stable owner.
    - all SQL is parameterized, content and tags are indexed in one commit.
    """
    ensure_local_lab(conn)
    _check_identity(memory_id, owner_id)
    if not isinstance(revision, str) or not revision:
        raise SyntheticIndexError("LAB_INVALID_REVISION")
    if expected_revision is not None and (
        not isinstance(expected_revision, str) or not expected_revision
        or expected_revision == revision
    ):
        raise SyntheticIndexError("LAB_INVALID_EXPECTED_REVISION")
    if summary is not None and not isinstance(summary, str):
        raise SyntheticIndexError("LAB_INVALID_SUMMARY")
    if privacy_scope not in ("PERSONAL", "PRIVATE", "SOCIAL", "PUBLIC", "AUTH", "SECRET"):
        raise SyntheticIndexError("LAB_INVALID_PRIVACY_SCOPE")
    if not isinstance(tags, tuple) or any(
        not isinstance(t, str) or not t for t in tags
    ):
        raise SyntheticIndexError("LAB_INVALID_TAGS")
    if len(set(tags)) != len(tags):
        raise SyntheticIndexError("LAB_DUPLICATE_TAG")
    # Exactly the canonical MemoryStore.put() FTS document layout.
    search_content = " ".join(filter(None, (content, summary or "")))
    search_tags = " ".join(sorted(tags))
    length, postings = _document_terms(search_content, search_tags)

    with conn.transaction():
        if expected_revision is None:
            row = conn.execute("""
                INSERT INTO amora_pg_v18_lab.memory_records(
                    id,kind,content,summary,provenance_kind,
                    provenance_source,provenance_confidence,
                    actor_id,privacy_scope,owner_entity_id,
                    remote_processing_allowed,created_at,updated_at,
                    importance,gate_score
                ) VALUES (
                    %s,'semantic',%s,%s,'user_assertion','synthetic:atomic-index',
                    1.0,%s,%s,%s,%s,%s,%s,0.8,0.8
                ) ON CONFLICT(id) DO NOTHING RETURNING id
            """, (
                memory_id, content, summary, owner_id, privacy_scope, owner_id,
                int(remote_allowed), revision, revision,
            )).fetchone()
            if row is None:
                raise SyntheticIndexError("LAB_MEMORY_ALREADY_EXISTS")
            operation = "created"
        else:
            current = conn.execute("""
                SELECT updated_at,owner_entity_id
                FROM amora_pg_v18_lab.memory_records WHERE id=%s
                FOR UPDATE
            """, (memory_id,)).fetchone()
            if current is None:
                raise SyntheticIndexError("LAB_MEMORY_MISSING")
            if current[1] != owner_id:
                raise SyntheticIndexError("LAB_OWNER_MISMATCH")
            if current[0] != expected_revision:
                raise SyntheticIndexError("LAB_STALE_REVISION")
            conn.execute("""
                UPDATE amora_pg_v18_lab.memory_records SET
                    content=%s,summary=%s,privacy_scope=%s,
                    remote_processing_allowed=%s,updated_at=%s
                WHERE id=%s
            """, (content, summary, privacy_scope, int(remote_allowed),
                  revision, memory_id))
            operation = "updated"

        conn.execute(
            "DELETE FROM amora_pg_v18_lab.memory_tags WHERE memory_id=%s",
            (memory_id,),
        )
        if tags:
            with conn.cursor() as cursor:
                cursor.executemany("""
                    INSERT INTO amora_pg_v18_lab.memory_tags(memory_id,tag)
                    VALUES (%s,%s)
                """, [(memory_id, tag) for tag in sorted(tags)])
        conn.execute("""
            INSERT INTO amora_pg_v18_lab.memory_search(memory_id,content,tags)
            VALUES (%s,%s,%s)
            ON CONFLICT(memory_id) DO UPDATE
                SET content=EXCLUDED.content,tags=EXCLUDED.tags
        """, (memory_id, search_content, search_tags))
        conn.execute("""
            INSERT INTO amora_pg_v18_lab.fts_doc_tokens(
                memory_id,document_length
            ) VALUES (%s,%s)
            ON CONFLICT(memory_id) DO UPDATE
                SET document_length=EXCLUDED.document_length
        """, (memory_id, length))
        conn.execute(
            "DELETE FROM amora_pg_v18_lab.fts_term_postings WHERE memory_id=%s",
            (memory_id,),
        )
        if postings:
            with conn.cursor() as cursor:
                cursor.executemany("""
                    INSERT INTO amora_pg_v18_lab.fts_term_postings(
                        memory_id,term,frequency
                    ) VALUES (%s,%s,%s)
                """, [(memory_id, term, frequency)
                      for term, frequency in postings])
        # Verify key index invariants before transaction can commit.
        token_sum = conn.execute("""
            SELECT COALESCE(SUM(frequency),0)
            FROM amora_pg_v18_lab.fts_term_postings WHERE memory_id=%s
        """, (memory_id,)).fetchone()[0]
        if token_sum != length:
            raise SyntheticIndexError("LAB_POSTING_LENGTH_DRIFT")
        tag_count = conn.execute("""
            SELECT COUNT(*) FROM amora_pg_v18_lab.memory_tags
            WHERE memory_id=%s
        """, (memory_id,)).fetchone()[0]
        if tag_count != len(tags):
            raise SyntheticIndexError("LAB_TAG_COUNT_DRIFT")
    return operation


def archive_synthetic_memory(
    conn, *, memory_id: str, owner_id: str,
    expected_revision: str, revision: str,
) -> None:
    """Archive without removing postings: canonical FTS5 corpus remains intact."""
    ensure_local_lab(conn)
    _check_identity(memory_id, owner_id)
    if not expected_revision or not revision or expected_revision == revision:
        raise SyntheticIndexError("LAB_INVALID_EXPECTED_REVISION")
    with conn.transaction():
        row = conn.execute("""
            SELECT owner_entity_id,updated_at,archived_at
            FROM amora_pg_v18_lab.memory_records WHERE id=%s FOR UPDATE
        """, (memory_id,)).fetchone()
        if row is None:
            raise SyntheticIndexError("LAB_MEMORY_MISSING")
        if row[0] != owner_id:
            raise SyntheticIndexError("LAB_OWNER_MISMATCH")
        if row[1] != expected_revision:
            raise SyntheticIndexError("LAB_STALE_REVISION")
        if row[2] is not None:
            raise SyntheticIndexError("LAB_MEMORY_ALREADY_ARCHIVED")
        conn.execute("""
            UPDATE amora_pg_v18_lab.memory_records
            SET archived_at=%s,updated_at=%s WHERE id=%s
        """, (revision, revision, memory_id))


def hard_delete_synthetic_memory(
    conn, *, memory_id: str, owner_id: str, expected_revision: str,
) -> None:
    """Remove a synthetic memory, relying on validated cascading FKs."""
    ensure_local_lab(conn)
    _check_identity(memory_id, owner_id)
    if not expected_revision:
        raise SyntheticIndexError("LAB_INVALID_EXPECTED_REVISION")
    with conn.transaction():
        row = conn.execute("""
            SELECT owner_entity_id,updated_at
            FROM amora_pg_v18_lab.memory_records WHERE id=%s FOR UPDATE
        """, (memory_id,)).fetchone()
        if row is None:
            raise SyntheticIndexError("LAB_MEMORY_MISSING")
        if row[0] != owner_id:
            raise SyntheticIndexError("LAB_OWNER_MISMATCH")
        if row[1] != expected_revision:
            raise SyntheticIndexError("LAB_STALE_REVISION")
        conn.execute(
            "DELETE FROM amora_pg_v18_lab.memory_records WHERE id=%s",
            (memory_id,),
        )
        # FK cascades must remove tags/search/document/postings together.
        for table in ("memory_tags", "memory_search",
                      "fts_doc_tokens", "fts_term_postings"):
            if conn.execute(
                f"SELECT 1 FROM amora_pg_v18_lab.{table} WHERE memory_id=%s",
                (memory_id,),
            ).fetchone() is not None:
                raise SyntheticIndexError("LAB_CASCADE_INCOMPLETE")
