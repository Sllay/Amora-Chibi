"""Opt-in, disposable PostgreSQL 18 memory projection coherence fence.

Not a serving migration. Install only AFTER the synthetic v18 import and FTS
postings setup, so all future projection-relevant writes atomically advance the
memory revision. A restored snapshot must rotate its epoch before serving.
"""
from __future__ import annotations

from uuid import uuid4

from .postgres_memory_lab import ensure_local_lab

_SCHEMA = "amora_pg_v18_lab"
# Err on the side of invalidating a replica too often. Every table below can
# affect text, ranking, identities, or owner/transport disclosure eligibility.
_RELEVANT_TABLES = (
    "memory_records", "memory_entities", "memory_tags", "memory_links",
    "memory_search", "fts_doc_tokens", "fts_term_postings",
    "memory_reflection_invalidations", "conversation_messages",
    "entities", "relationships",
)


def memory_epoch_schema_statements(epoch: str) -> tuple[tuple[str, tuple], ...]:
    """Audited DDL/parameters for the memory projection revision fence.

    Shared by disposable labs and explicitly authorized empty staging prep.
    """
    statements: list[tuple[str, tuple]] = [
        ("""
            CREATE TABLE amora_pg_v18_lab.amora_memory_projection_epoch(
                singleton INTEGER PRIMARY KEY CHECK(singleton=1),
                epoch_id TEXT NOT NULL CHECK(length(epoch_id)>0),
                revision BIGINT NOT NULL CHECK(revision>=0)
            )
        """, ()),
        (
            "INSERT INTO amora_pg_v18_lab.amora_memory_projection_epoch "
            "(singleton,epoch_id,revision) VALUES (1,%s,0)", (epoch,),
        ),
        ("""
            CREATE FUNCTION amora_pg_v18_lab.bump_memory_projection_epoch()
            RETURNS TRIGGER AS $$
            BEGIN
                UPDATE amora_pg_v18_lab.amora_memory_projection_epoch
                SET revision=revision+1 WHERE singleton=1;
                IF NOT FOUND THEN
                    RAISE EXCEPTION 'PG_MEMORY_EPOCH_NOT_INSTALLED';
                END IF;
                RETURN NULL;
            END;
            $$ LANGUAGE plpgsql
        """, ()),
    ]
    for table in _RELEVANT_TABLES:
        for verb in ("INSERT", "UPDATE", "DELETE"):
            statements.append((
                f"CREATE TRIGGER epoch_{table}_{verb.lower()} "
                f"AFTER {verb} ON {_SCHEMA}.{table} FOR EACH ROW "
                f"EXECUTE FUNCTION {_SCHEMA}.bump_memory_projection_epoch()",
                (),
            ))
    return tuple(statements)


def install_pg_memory_epoch_lab(raw) -> str:
    """Create a transactional, monotonic epoch in an isolated PG18 schema."""
    ensure_local_lab(raw)
    if getattr(raw, "autocommit", False) is not True:
        raise ValueError("PG_EPOCH_SYNTHETIC_AUTOCOMMIT_REQUIRED")
    epoch = str(uuid4())
    with raw.transaction():
        for sql, parameters in memory_epoch_schema_statements(epoch):
            if parameters:
                raw.execute(sql, parameters)
            else:
                raw.execute(sql)
    return epoch


def rotate_pg_memory_epoch_after_restore(raw) -> str:
    """Operator-only fencing action; never auto-run on ordinary reconnect."""
    ensure_local_lab(raw)
    if getattr(raw, "autocommit", False) is not True:
        raise ValueError("PG_EPOCH_SYNTHETIC_AUTOCOMMIT_REQUIRED")
    rotated = str(uuid4())
    with raw.transaction():
        rows = raw.execute("""
            UPDATE amora_pg_v18_lab.amora_memory_projection_epoch
            SET epoch_id=%s,revision=revision+1
            WHERE singleton=1 RETURNING epoch_id
        """, (rotated,)).fetchall()
        if len(rows)!=1:
            raise RuntimeError("PG_MEMORY_EPOCH_NOT_INSTALLED")
    return rotated
