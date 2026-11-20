"""PostgreSQL 18 synthetic memory laboratory, NEVER the serving Core backend.

No DSN handling, no migration, no network setup and no credentials here.
Only CI loopback 'amora_pg_synthetic' connections can install or mutate the
isolated amora_pg_lab schema. This is a limited semantic slice, NOT v18 DDL.
"""
from __future__ import annotations

from datetime import datetime, timezone

_LAB_SCHEMA = "amora_pg_lab"
_LAB_DATABASE = "amora_pg_synthetic"
_LOCAL_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

# The names and column types below are deliberately explicit and reviewed
# against canonical v18 migrations. There is no SQLite -> PG regex converter.
# TEXT JSON and ISO timestamps preserve existing serializations for this lab.
# The live 51-table migration has NOT been implemented.
LAB_DDL = (
    "CREATE SCHEMA amora_pg_lab",
    """
    CREATE TABLE amora_pg_lab.events (
        id TEXT PRIMARY KEY,
        schema_version BIGINT NOT NULL,
        event_type TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        source TEXT NOT NULL,
        actor TEXT, target TEXT,
        confidence DOUBLE PRECISION NOT NULL,
        importance DOUBLE PRECISION NOT NULL,
        priority BIGINT NOT NULL,
        payload_json TEXT NOT NULL,
        privacy_scope TEXT NOT NULL,
        session_id TEXT, correlation_id TEXT, causation_id TEXT,
        causal_parent TEXT, sequence BIGINT, dedupe_key TEXT UNIQUE
    )
    """,
    """
    CREATE TABLE amora_pg_lab.memory_records (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN ('episodic','semantic','relational')),
        content TEXT NOT NULL,
        summary TEXT,
        provenance_kind TEXT NOT NULL,
        provenance_source TEXT NOT NULL,
        provenance_confidence DOUBLE PRECISION NOT NULL
            CHECK(provenance_confidence BETWEEN 0 AND 1),
        source_event_id TEXT REFERENCES amora_pg_lab.events(id) ON DELETE SET NULL,
        actor_id TEXT,
        session_id TEXT,
        derived_from_json TEXT NOT NULL DEFAULT '[]',
        privacy_scope TEXT NOT NULL,
        owner_entity_id TEXT,
        audience_json TEXT NOT NULL DEFAULT '[]',
        remote_processing_allowed BIGINT NOT NULL DEFAULT 1
            CHECK(remote_processing_allowed IN (0, 1)),
        valid_from TEXT,
        valid_until TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        importance DOUBLE PRECISION NOT NULL CHECK(importance BETWEEN 0 AND 1),
        gate_score DOUBLE PRECISION NOT NULL CHECK(gate_score BETWEEN 0 AND 1),
        retrievability DOUBLE PRECISION NOT NULL DEFAULT 1
            CHECK(retrievability BETWEEN 0 AND 1),
        access_count BIGINT NOT NULL DEFAULT 0 CHECK(access_count >= 0),
        last_accessed_at TEXT,
        archived_at TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )
    """,
    """
    CREATE INDEX lab_memory_kind_created
       ON amora_pg_lab.memory_records(kind, created_at DESC)
    """,
    """
    CREATE INDEX lab_memory_source_event
       ON amora_pg_lab.memory_records(source_event_id)
    """,
    """
    CREATE INDEX lab_memory_validity
       ON amora_pg_lab.memory_records(valid_from, valid_until)
    """,
    """
    CREATE INDEX lab_memory_retrievability
       ON amora_pg_lab.memory_records(archived_at, retrievability DESC, importance DESC)
    """,
    """
    CREATE TABLE amora_pg_lab.memory_entities (
       memory_id TEXT NOT NULL REFERENCES amora_pg_lab.memory_records(id) ON DELETE CASCADE,
       entity_id TEXT NOT NULL,
       confidence DOUBLE PRECISION NOT NULL CHECK(confidence BETWEEN 0 AND 1),
       PRIMARY KEY(memory_id, entity_id)
    )
    """,
    """
    CREATE INDEX lab_memory_entities_entity
       ON amora_pg_lab.memory_entities(entity_id, confidence DESC)
    """,
    """
    CREATE TABLE amora_pg_lab.memory_tags (
       memory_id TEXT NOT NULL REFERENCES amora_pg_lab.memory_records(id) ON DELETE CASCADE,
       tag TEXT NOT NULL,
       PRIMARY KEY(memory_id, tag)
    )
    """,
    """
    CREATE INDEX lab_memory_tags_tag
       ON amora_pg_lab.memory_tags(tag)
    """,
    """
    CREATE TABLE amora_pg_lab.memory_links (
       source_memory_id TEXT NOT NULL REFERENCES amora_pg_lab.memory_records(id)
           ON DELETE CASCADE,
       target_memory_id TEXT NOT NULL REFERENCES amora_pg_lab.memory_records(id)
           ON DELETE CASCADE,
       link_type TEXT NOT NULL,
       confidence DOUBLE PRECISION NOT NULL CHECK(confidence BETWEEN 0 AND 1),
       PRIMARY KEY(source_memory_id, target_memory_id, link_type),
       CHECK(source_memory_id <> target_memory_id)
    )
    """,
    """
    CREATE INDEX lab_memory_links_target
       ON amora_pg_lab.memory_links(target_memory_id, link_type)
    """,
    # SQLite FTS5's unicode61 and bm25 have no identical PostgreSQL analogue.
    # This is a GIN-backed query candidate experiment, not ranking parity.
    # Store the same concatenated text and tags as MemoryStore.put().
    """
    CREATE TABLE amora_pg_lab.memory_search (
       memory_id TEXT PRIMARY KEY REFERENCES amora_pg_lab.memory_records(id)
           ON DELETE CASCADE,
       content TEXT NOT NULL,
       tags TEXT NOT NULL
    )
    """,
    """
    CREATE INDEX lab_memory_search_gin
       ON amora_pg_lab.memory_search
       USING GIN (to_tsvector('simple'::regconfig, content || ' ' || tags))
    """,
)


def ensure_local_lab(conn) -> None:
    """Reject non-local/production connections before any SQL, including DDL."""
    info = conn.info
    if (info.host not in _LOCAL_HOSTS
            or info.dbname != _LAB_DATABASE
            or info.server_version // 10000 != 18):
        raise ValueError("POSTGRES_LAB_LOCAL_DB_REQUIRED")


def install_lab(conn) -> None:
    ensure_local_lab(conn)
    # A dedicated schema with no IF NOT EXISTS: never adopt stale objects.
    with conn.transaction():
        for statement in LAB_DDL:
            conn.execute(statement)


def put_synthetic_memory(
    conn, *, memory_id: str, owner: str, content: str, tags: tuple[str, ...] = (),
    scope: str = "PRIVATE", remote_allowed: bool = False,
    archived_at: str | None = None, metadata_json: str = "{}",
) -> None:
    """Write one synthetic memory and search document atomically.

    The production MemoryStore does NOT call this helper. Caller passes only
    invented test records to this function; never production snapshots.
    """
    ensure_local_lab(conn)
    stamp = datetime(2026, 1, 1, tzinfo=timezone.utc).isoformat()
    with conn.transaction():
        conn.execute(
            """
            INSERT INTO amora_pg_lab.memory_records (
                id, kind, content, provenance_kind, provenance_source,
                provenance_confidence, privacy_scope, owner_entity_id,
                remote_processing_allowed, created_at, updated_at,
                importance, gate_score, archived_at, metadata_json
            ) VALUES (
                %s, 'semantic', %s, 'observation', 'synthetic:pg-lab',
                1.0, %s, %s, %s, %s, %s, 0.8, 0.8, %s, %s
            )
            """,
            (memory_id, content, scope, owner, int(remote_allowed),
             stamp, stamp, archived_at, metadata_json),
        )
        conn.execute(
            """
            INSERT INTO amora_pg_lab.memory_search(memory_id, content, tags)
            VALUES (%s, %s, %s)
            """,
            (memory_id, content, " ".join(sorted(tags))),
        )
        if tags:
            with conn.cursor() as cursor:
                cursor.executemany(
                    """
                    INSERT INTO amora_pg_lab.memory_tags(memory_id, tag)
                    VALUES (%s, %s)
                    """,
                    [(memory_id, tag) for tag in sorted(tags)],
                )


def candidate_ids(
    conn, *, owner: str, query: str, remote_only: bool = False, limit: int = 10
) -> list[str]:
    """A PostgreSQL FTS candidate experiment, NOT canonical RRF/MMR ranking.

    Always filter owner/privacy/archives BEFORE applying the ranking cap.
    AUTH/SECRET are prohibited regardless of remote_only.
    """
    ensure_local_lab(conn)
    if not 1 <= limit <= 150:
        raise ValueError("POSTGRES_LAB_INVALID_LIMIT")
    rows = conn.execute(
        """
        SELECT m.id
          FROM amora_pg_lab.memory_records AS m
          JOIN amora_pg_lab.memory_search AS s ON s.memory_id = m.id
         WHERE m.owner_entity_id = %s
           AND m.privacy_scope IN ('PERSONAL','PRIVATE','SOCIAL','PUBLIC')
           AND m.archived_at IS NULL
           AND (%s = FALSE OR m.remote_processing_allowed = 1)
           AND to_tsvector('simple'::regconfig, s.content || ' ' || s.tags)
               @@ plainto_tsquery('simple'::regconfig, %s)
         ORDER BY ts_rank(
             to_tsvector('simple'::regconfig, s.content || ' ' || s.tags),
             plainto_tsquery('simple'::regconfig, %s)
           ) DESC, m.created_at DESC, m.id DESC
         LIMIT %s
        """,
        (owner, remote_only, query, query, limit),
    ).fetchall()
    return [row[0] for row in rows]
