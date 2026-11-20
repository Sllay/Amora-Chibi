"""Reviewed PostgreSQL mappings for the original ConversationStore SQL.

Every key is the exact canonical SQLite SQL after whitespace normalization.
Every value is hand-reviewed PostgreSQL 18 SQL. No generic qmark rewriting,
DDL translation, global SQL parser or automatic runtime activation.

The candidate must remain synthetic-only until principal authorization,
full Core routing and voice cancellation contracts are certified.
"""
from __future__ import annotations

from .postgres_runtime_connection import ReviewedStatement, PostgresRuntimeError

def _key(sql: str) -> str:
    return " ".join(sql.split())


def conversation_statements(*, schema: str = "amora_pg_v18_lab") -> dict[str, ReviewedStatement]:
    """Canonical ConversationStore v18 reads and writes, including voice leases."""
    if schema != "amora_pg_v18_lab":
        # Explicitly do not permit unreviewed production schema bindings.
        raise PostgresRuntimeError("PG_CONVERSATION_SCHEMA_NOT_REVIEWED")
    table = "amora_pg_v18_lab."
    statements: dict[str, ReviewedStatement] = {}

    def add(source: str, pg_sql: str, parameters: int, limit: int = 1000) -> None:
        key = _key(source)
        if key in statements:
            raise PostgresRuntimeError("PG_CONVERSATION_DUPLICATE_SQL_KEY")
        statements[key] = ReviewedStatement(pg_sql, parameters, limit)

    add("""
        INSERT INTO conversation_turn_decisions(id, state, score, result_json, created_at, dedupe_key)
        VALUES (?, ?, ?, ?, ?, ?)
    """, """
        INSERT INTO amora_pg_v18_lab.conversation_turn_decisions(
            id, state, score, result_json, created_at, dedupe_key
        ) VALUES (%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING
    """, 6)
    add("SELECT result_json FROM conversation_turn_decisions WHERE dedupe_key = ?",
        "SELECT result_json FROM " + table + "conversation_turn_decisions WHERE dedupe_key=%s", 1, 1)
    add("SELECT result_json FROM conversation_turn_decisions ORDER BY created_at DESC LIMIT ?",
        "SELECT result_json FROM " + table + "conversation_turn_decisions ORDER BY created_at DESC LIMIT %s", 1, 200)
    add("SELECT COUNT(*) FROM conversation_turn_decisions",
        "SELECT COUNT(*) FROM " + table + "conversation_turn_decisions", 0, 1)

    add("SELECT utterance_json FROM speech_utterances WHERE device_id = ? AND status = ? AND dedupe_key LIKE ?",
        "SELECT utterance_json FROM " + table +
        "speech_utterances WHERE device_id=%s AND status=%s AND dedupe_key LIKE %s", 3, 10000)
    add("""
        INSERT INTO speech_utterances(
            id, target_id, kind, priority, privacy_scope, device_id, status,
            utterance_json, created_at, claimed_at, claim_expires_at, spoken_at, dedupe_key
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, """
        INSERT INTO amora_pg_v18_lab.speech_utterances(
            id,target_id,kind,priority,privacy_scope,device_id,status,
            utterance_json,created_at,claimed_at,claim_expires_at,spoken_at,dedupe_key
        ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT DO NOTHING
    """, 13)
    add("SELECT utterance_json FROM speech_utterances WHERE dedupe_key = ?",
        "SELECT utterance_json FROM " + table + "speech_utterances WHERE dedupe_key=%s", 1, 1)
    add("SELECT utterance_json FROM speech_utterances WHERE id = ?",
        # Lock a row through a state transition; an autocommit read releases
        # the lock immediately and retains the canonical read API.
        "SELECT utterance_json FROM " + table + "speech_utterances WHERE id=%s FOR UPDATE", 1, 1)
    add("SELECT utterance_json FROM speech_utterances ORDER BY created_at DESC LIMIT ?",
        "SELECT utterance_json FROM " + table + "speech_utterances ORDER BY created_at DESC LIMIT %s", 1, 200)
    add("SELECT COUNT(*) FROM speech_utterances WHERE status IN (?, ?)",
        "SELECT COUNT(*) FROM " + table + "speech_utterances WHERE status IN (%s,%s)", 2, 1)
    add("SELECT COUNT(*) FROM speech_utterances WHERE status = ?",
        "SELECT COUNT(*) FROM " + table + "speech_utterances WHERE status=%s", 1, 1)

    add("SELECT id, utterance_json FROM speech_utterances WHERE status = ? AND claim_expires_at IS NOT NULL AND claim_expires_at <= ?",
        "SELECT id,utterance_json FROM " + table +
        "speech_utterances WHERE status=%s AND claim_expires_at IS NOT NULL "
        "AND claim_expires_at<=%s FOR UPDATE SKIP LOCKED", 2, 10000)
    add("SELECT id, utterance_json FROM speech_utterances WHERE device_id = ? AND status = ? ORDER BY priority DESC, created_at ASC LIMIT ?",
        "SELECT id,utterance_json FROM " + table +
        "speech_utterances WHERE device_id=%s AND status=%s "
        "ORDER BY priority DESC,created_at ASC LIMIT %s FOR UPDATE SKIP LOCKED", 3, 32)
    add("SELECT utterance_json FROM speech_utterances WHERE status IN (?, ?)",
        "SELECT utterance_json FROM " + table +
        "speech_utterances WHERE status IN (%s,%s) FOR UPDATE SKIP LOCKED", 2, 10000)
    add("""
        UPDATE speech_utterances SET
            status = ?, utterance_json = ?, claimed_at = ?, claim_expires_at = ?, spoken_at = ?
        WHERE id = ?
    """, """
        UPDATE amora_pg_v18_lab.speech_utterances
        SET status=%s, utterance_json=%s, claimed_at=%s,
            claim_expires_at=%s, spoken_at=%s WHERE id=%s
    """, 6)
    return statements
