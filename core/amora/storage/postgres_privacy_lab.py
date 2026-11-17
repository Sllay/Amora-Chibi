"""Isolated PostgreSQL 18 privacy/JSON query parity experiments.

These queries mirror selected v18 MemoryStore SQL predicates against an
*invented* fixture after synthetic import. They are NOT a production dialect
adapter and are never registered with the serving Core. There is no DSN, file
path, network creation or use of the PCS.4C backup.
"""
from __future__ import annotations

from .postgres_memory_lab import ensure_local_lab

_SCHEMA = "amora_pg_v18_lab"


def projection_candidate_ids(
    conn, *, now: str, recipient_id: str | None = None,
    public_only: bool = False, remote_only: bool = False,
    include_archived: bool = False, kinds: tuple[str, ...] = (),
    after: str | None = None, before: str | None = None,
) -> list[str]:
    """Match MemoryStore._candidate_rows *baseline*, before lexical search.

    The 300-record limit is canonical; the FTS5 lexical expansion (+150)
    is explicitly NOT part of this experiment. Audience check uses JSON array
    elements, never substring matching. AUTH/SECRET are not disclosed.
    """
    ensure_local_lab(conn)
    where = ["(m.valid_until IS NULL OR m.valid_until >= %s)"]
    values: list[object] = [now]
    if not include_archived:
        where.append("m.archived_at IS NULL")
    if kinds:
        where.append("m.kind = ANY(%s::text[])")
        values.append(list(sorted(set(kinds))))
    if after is not None:
        where.append("m.created_at >= %s")
        values.append(after)
    if before is not None:
        where.append("m.created_at <= %s")
        values.append(before)
    if public_only:
        where.append("m.privacy_scope = 'PUBLIC'")
    if remote_only:
        where.append("m.remote_processing_allowed = 1")
    if recipient_id and not public_only:
        where.append("m.privacy_scope NOT IN ('AUTH','SECRET')")
        where.append(
            """(
                m.privacy_scope = 'PUBLIC'
                OR m.owner_entity_id = %s
                OR EXISTS (
                    SELECT 1 FROM jsonb_array_elements_text(
                        m.audience_json::jsonb
                    ) AS audience(member)
                    WHERE audience.member = %s
                )
                OR (
                    COALESCE(m.owner_entity_id, '') = ''
                    AND jsonb_array_length(m.audience_json::jsonb) = 0
                    AND m.actor_id = %s
                )
            )"""
        )
        values.extend([recipient_id] * 3)
    query = (
        "SELECT m.id FROM " + _SCHEMA + ".memory_records AS m WHERE "
        + " AND ".join(where)
        + " ORDER BY (m.importance * m.retrievability) DESC,"
        + " m.created_at DESC, m.id LIMIT 300"
    )
    return [row[0] for row in conn.execute(query, tuple(values)).fetchall()]


def owner_turn_memory_ids(conn, *, owner_id: str, turn_id: str) -> list[str]:
    """PG equivalent of canonical owner_turn_memory_status's row selection."""
    ensure_local_lab(conn)
    rows = conn.execute(
        """
        SELECT id FROM amora_pg_v18_lab.memory_records
        WHERE owner_entity_id=%s
          AND privacy_scope NOT IN ('AUTH','SECRET')
          AND metadata_json::jsonb ->> 'turn_id' = %s
        ORDER BY created_at,id LIMIT 50
        """,
        (owner_id, turn_id),
    ).fetchall()
    return [row[0] for row in rows]


def shareable_owner_record_id(conn, *, owner_id: str, now: str,
                              memory_id: str) -> str | None:
    """Manually translated v18 SHAREABLE_MEMORY_SQL one-turn disclosure gate.

    This method only returns an ID. It does NOT hydrate or disclose content.
    """
    ensure_local_lab(conn)
    row = conn.execute(
        """
        SELECT m.id
          FROM amora_pg_v18_lab.memory_records AS m
         WHERE m.owner_entity_id=%s AND m.privacy_scope='PERSONAL'
           AND m.archived_at IS NULL
           AND (m.valid_from IS NULL OR m.valid_from<=%s)
           AND (m.valid_until IS NULL OR m.valid_until>=%s)
           AND jsonb_array_length(m.derived_from_json::jsonb)=0
           AND NOT EXISTS (
               SELECT 1 FROM jsonb_array_elements_text(
                   m.audience_json::jsonb
               ) AS audience(member) WHERE audience.member<>m.owner_entity_id
           )
           AND (
               (m.provenance_kind='user_assertion'
                   AND m.actor_id=m.owner_entity_id)
               OR (
                   m.provenance_kind='inference' AND m.actor_id='ENTITY_AMORA'
                   AND m.provenance_source LIKE 'cognition:%%'
                   AND EXISTS (
                       SELECT 1 FROM amora_pg_v18_lab.conversation_messages AS s
                        WHERE s.turn_id=m.metadata_json::jsonb ->> 'turn_id'
                          AND s.dedupe_key='cognitive-input:' || s.turn_id
                          AND s.speaker_id=m.owner_entity_id
                          AND s.session_id=m.session_id
                          AND s.privacy_scope='PERSONAL'
                          AND s.metadata_json::jsonb ->> 'surface'='platform_messaging'
                          AND s.metadata_json::jsonb ->> 'channel_type'='dm'
                          AND COALESCE(
                              (s.metadata_json::jsonb ->> 'test_mode')::boolean,
                              FALSE
                          )=FALSE
                          AND NOT EXISTS (
                             SELECT 1
                               FROM amora_pg_v18_lab.memory_reflection_invalidations x
                              WHERE x.session_id=s.session_id
                                AND x.native_message_hash=
                                    s.metadata_json::jsonb ->> 'native_message_id_hash'
                                AND x.invalidated_through>=COALESCE(
                                    (s.metadata_json::jsonb ->> 'transport_revision')::bigint,
                                    1
                                )
                          )
                   )
               )
           )
           AND m.id=%s
         LIMIT 1
        """,
        (owner_id, now, now, memory_id),
    ).fetchone()
    return row[0] if row is not None else None
