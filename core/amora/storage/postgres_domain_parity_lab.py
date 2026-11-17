"""Phase 2E.2: explicit identity and conversation SQL parity experiments.

Disposable PG18 loopback lab only. No DSN, no connector to Turso/Aiven,
no Core backend selector and no real data path. These operations are compared
with the canonical SQLite v18 semantics using exclusively invented fixtures.

This is not a serving registry, conversation or Discord runtime adapter.
Speech leases, session authentication, user authorization and all other
query families remain separate release gates.
"""
from __future__ import annotations

import json
from collections.abc import Iterable

from .postgres_memory_lab import ensure_local_lab


class DomainLabError(ValueError):
    """Fixed codes; never include private values in exception messages."""


def _str(value: str, *, max_length: int = 256) -> str:
    if not isinstance(value, str) or not 0 < len(value) <= max_length:
        raise DomainLabError("DOMAIN_LAB_INVALID_IDENTITY")
    return value


def _json_object(value: str) -> str:
    if not isinstance(value, str) or len(value) > 32_768:
        raise DomainLabError("DOMAIN_LAB_INVALID_JSON")
    try:
        result = json.loads(value)
    except (ValueError, TypeError) as exc:
        raise DomainLabError("DOMAIN_LAB_INVALID_JSON") from exc
    if not isinstance(result, dict):
        raise DomainLabError("DOMAIN_LAB_JSON_OBJECT_REQUIRED")
    return value


def save_entity(
    conn, *, entity_id: str, canonical_name: str,
    entity_type: str, aliases: tuple[str, ...] = (),
) -> None:
    """Mirror the atomic entity+aliases save_registry domain subtransaction.

    Unique aliases cannot be reassigned to another identity. Any collision
    must roll back the canonical-name update and alias removals as well.
    """
    ensure_local_lab(conn)
    _str(entity_id)
    _str(canonical_name)
    _str(entity_type)
    if not isinstance(aliases, tuple) or len(aliases) > 64:
        raise DomainLabError("DOMAIN_LAB_INVALID_ALIASES")
    if any(not isinstance(name, str) or not 0 < len(name) <= 256
           for name in aliases):
        raise DomainLabError("DOMAIN_LAB_INVALID_ALIASES")
    # Database.save_registry dedupes the normalized name set.
    all_aliases = sorted(set(aliases) | {canonical_name})
    with conn.transaction():
        conn.execute("""
            INSERT INTO amora_pg_v18_lab.entities(
                id, canonical_name, entity_type, payload_json
            ) VALUES (%s,%s,%s,'{}')
            ON CONFLICT(id) DO UPDATE SET
                canonical_name=EXCLUDED.canonical_name,
                entity_type=EXCLUDED.entity_type
        """, (entity_id, canonical_name, entity_type))
        conn.execute(
            "DELETE FROM amora_pg_v18_lab.aliases WHERE entity_id=%s",
            (entity_id,),
        )
        with conn.cursor() as cursor:
            cursor.executemany("""
                INSERT INTO amora_pg_v18_lab.aliases(normalized_alias,entity_id)
                VALUES (%s,%s)
            """, [(alias, entity_id) for alias in all_aliases])


def save_relationship(
    conn, *, source_id: str, relation: str,
    target_id: str, confidence: float,
) -> None:
    """Preserve canonical UNIQUE(source, relation_type, target) upsert."""
    ensure_local_lab(conn)
    for identifier in (source_id, relation, target_id):
        _str(identifier)
    if type(confidence) not in (float, int) or not 0 <= confidence <= 1:
        raise DomainLabError("DOMAIN_LAB_INVALID_CONFIDENCE")
    with conn.transaction():
        conn.execute("""
            INSERT INTO amora_pg_v18_lab.relationships(
                source,relation_type,target,confidence
            ) VALUES (%s,%s,%s,%s)
            ON CONFLICT(source,relation_type,target)
            DO UPDATE SET confidence=EXCLUDED.confidence
        """, (source_id, relation, target_id, confidence))


def registry_rows(conn) -> tuple[list[tuple], list[tuple], list[tuple]]:
    """Read the lab-only canonical registry in deterministic order."""
    ensure_local_lab(conn)
    entities = conn.execute("""
        SELECT id,canonical_name,entity_type
        FROM amora_pg_v18_lab.entities ORDER BY id
    """).fetchall()
    aliases = conn.execute("""
        SELECT normalized_alias,entity_id
        FROM amora_pg_v18_lab.aliases ORDER BY normalized_alias,entity_id
    """).fetchall()
    relationships = conn.execute("""
        SELECT source,relation_type,target,confidence
        FROM amora_pg_v18_lab.relationships
        ORDER BY source,relation_type,target
    """).fetchall()
    return (
        [tuple(row) for row in entities],
        [tuple(row) for row in aliases],
        [tuple(row) for row in relationships],
    )


def put_turn_decision(
    conn, *, decision_id: str, state: str, score: float,
    payload_json: str, created_at: str, dedupe_key: str,
) -> bool:
    """Return False for a duplicate ID or dedupe key, as ConversationStore."""
    ensure_local_lab(conn)
    for key in (decision_id, state, created_at, dedupe_key):
        _str(key)
    if type(score) not in (float, int):
        raise DomainLabError("DOMAIN_LAB_INVALID_SCORE")
    _json_object(payload_json)
    with conn.transaction():
        row = conn.execute("""
            INSERT INTO amora_pg_v18_lab.conversation_turn_decisions(
                id,state,score,result_json,created_at,dedupe_key
            ) VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING id
        """, (decision_id, state, score, payload_json,
              created_at, dedupe_key)).fetchone()
    return row is not None


def get_turn_by_dedupe(conn, dedupe_key: str) -> str | None:
    """Laboratory projection of canonical get_turn_by_dedupe."""
    ensure_local_lab(conn)
    _str(dedupe_key)
    row = conn.execute("""
        SELECT result_json FROM amora_pg_v18_lab.conversation_turn_decisions
        WHERE dedupe_key=%s
    """, (dedupe_key,)).fetchone()
    return row[0] if row else None


def list_turn_results(conn, limit: int = 50) -> list[str]:
    ensure_local_lab(conn)
    if type(limit) is not int:
        raise DomainLabError("DOMAIN_LAB_INVALID_LIMIT")
    safe_limit = max(1, min(200, limit))
    rows = conn.execute("""
        SELECT result_json FROM amora_pg_v18_lab.conversation_turn_decisions
        ORDER BY created_at DESC, id ASC LIMIT %s
    """, (safe_limit,)).fetchall()
    return [row[0] for row in rows]


def put_message(
    conn, *, message_id: str, turn_id: str, speaker_id: str,
    session_id: str, text: str, metadata_json: str,
    started_at: str, dedupe_key: str, privacy_scope: str = "PERSONAL",
    remote_processing_allowed: bool = False,
) -> bool:
    """Insert-only synthetic message. Duplicate key never overwrites content."""
    ensure_local_lab(conn)
    for value in (message_id, turn_id, speaker_id,
                  session_id, started_at, dedupe_key):
        _str(value)
    if not isinstance(text, str) or len(text) > 32_768:
        raise DomainLabError("DOMAIN_LAB_INVALID_TEXT")
    _json_object(metadata_json)
    if privacy_scope not in ("PERSONAL", "PRIVATE", "SOCIAL", "PUBLIC",
                             "AUTH", "SECRET"):
        raise DomainLabError("DOMAIN_LAB_INVALID_PRIVACY_SCOPE")
    with conn.transaction():
        row = conn.execute("""
            INSERT INTO amora_pg_v18_lab.conversation_messages(
                id,turn_id,speaker_id,session_id,source,raw_text,
                normalized_text,privacy_scope,remote_processing_allowed,
                status,started_at,metadata_json,dedupe_key
            ) VALUES (
                %s,%s,%s,%s,'synthetic:domain-lab',%s,%s,%s,%s,
                'received',%s,%s,%s
            ) ON CONFLICT DO NOTHING RETURNING id
        """, (message_id, turn_id, speaker_id, session_id,
              text, text, privacy_scope, int(remote_processing_allowed),
              started_at, metadata_json, dedupe_key)).fetchone()
    return row is not None


def scoped_message(
    conn, *, message_id: str, session_id: str, speaker_id: str,
    remote_only: bool = False,
) -> tuple | None:
    """Return a synthetic message only for the exact speaker and session.

    Does not claim to implement the complete Core identity authorization
    policy. AUTH/SECRET are excluded. No cross-session or speaker fallback.
    """
    ensure_local_lab(conn)
    for value in (message_id, session_id, speaker_id):
        _str(value)
    row = conn.execute("""
        SELECT id,turn_id,raw_text,metadata_json,privacy_scope
        FROM amora_pg_v18_lab.conversation_messages
        WHERE id=%s AND session_id=%s AND speaker_id=%s
          AND privacy_scope NOT IN ('AUTH','SECRET')
          AND (%s=FALSE OR remote_processing_allowed=1)
    """, (message_id, session_id, speaker_id, bool(remote_only))).fetchone()
    return tuple(row) if row else None
