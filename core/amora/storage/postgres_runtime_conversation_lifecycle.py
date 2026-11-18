"""Reviewed PG18 transaction SQL for canonical conversation session/turn runtime.

These are immutable, schema-qualified mappings of canonical SQLite literals.
They are never translated at runtime and no production backend uses them.
"""
from __future__ import annotations

from .postgres_runtime_connection import ReviewedStatement

CANCEL_SOURCE = "PG_CONVERSATION_RUNTIME_CANCEL_SOURCE"


def conversation_runtime_statements() -> dict[str, ReviewedStatement]:
    out: dict[str, ReviewedStatement] = {}
    rows = (
        ("SELECT * FROM conversation_runtime_sessions WHERE platform=? AND account_scope=? AND conversation_id=?",
         "SELECT * FROM amora_pg_v18_lab.conversation_runtime_sessions WHERE platform=%s AND account_scope=%s AND conversation_id=%s", 3, 1),
        ("INSERT INTO conversation_runtime_sessions( platform, account_scope, conversation_id, session_id, state, attention_state, floor_owner_ref, floor_owner_entity_id, opened_at, last_activity_at, idle_at, dormant_at, closed_at, revision ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, NULL, NULL, NULL, 1)",
         "INSERT INTO amora_pg_v18_lab.conversation_runtime_sessions( platform, account_scope, conversation_id, session_id, state, attention_state, floor_owner_ref, floor_owner_entity_id, opened_at, last_activity_at, idle_at, dormant_at, closed_at, revision ) VALUES (%s, %s, %s, %s, 'active', %s, %s, %s, %s, %s, NULL, NULL, NULL, 1)", 9, 1),
        ("UPDATE conversation_runtime_sessions SET session_id=?, state='active', attention_state=?, floor_owner_ref=?, floor_owner_entity_id=?, opened_at=?, last_activity_at=?, idle_at=NULL, dormant_at=NULL, closed_at=NULL, revision=revision+1 WHERE platform=? AND account_scope=? AND conversation_id=?",
         "UPDATE amora_pg_v18_lab.conversation_runtime_sessions SET session_id=%s, state='active', attention_state=%s, floor_owner_ref=%s, floor_owner_entity_id=%s, opened_at=%s, last_activity_at=%s, idle_at=NULL, dormant_at=NULL, closed_at=NULL, revision=revision+1 WHERE platform=%s AND account_scope=%s AND conversation_id=%s", 9, 1),
        ("UPDATE conversation_runtime_sessions SET state='active', attention_state=?, floor_owner_ref=?, floor_owner_entity_id=?, last_activity_at=?, idle_at=NULL, dormant_at=NULL, revision=revision+1 WHERE platform=? AND account_scope=? AND conversation_id=?",
         "UPDATE amora_pg_v18_lab.conversation_runtime_sessions SET state='active', attention_state=%s, floor_owner_ref=%s, floor_owner_entity_id=%s, last_activity_at=%s, idle_at=NULL, dormant_at=NULL, revision=revision+1 WHERE platform=%s AND account_scope=%s AND conversation_id=%s", 7, 1),
        ("UPDATE conversation_runtime_sessions SET attention_state='background', floor_owner_ref=?, floor_owner_entity_id=?, last_activity_at=?, revision=revision+1 WHERE platform=? AND account_scope=? AND conversation_id=?",
         "UPDATE amora_pg_v18_lab.conversation_runtime_sessions SET attention_state='background', floor_owner_ref=%s, floor_owner_entity_id=%s, last_activity_at=%s, revision=revision+1 WHERE platform=%s AND account_scope=%s AND conversation_id=%s", 6, 1),
        ("UPDATE conversation_runtime_sessions SET state='closed', attention_state='disengaged', floor_owner_ref=NULL, floor_owner_entity_id=NULL, closed_at=?, last_activity_at=?, revision=revision+1 WHERE platform=? AND account_scope=? AND conversation_id=?",
         "UPDATE amora_pg_v18_lab.conversation_runtime_sessions SET state='closed', attention_state='disengaged', floor_owner_ref=NULL, floor_owner_entity_id=NULL, closed_at=%s, last_activity_at=%s, revision=revision+1 WHERE platform=%s AND account_scope=%s AND conversation_id=%s", 5, 1),
        ("UPDATE conversation_runtime_sessions SET state='dormant', attention_state='disengaged', floor_owner_ref=NULL, floor_owner_entity_id=NULL, dormant_at=?, revision=revision+1 WHERE platform=? AND account_scope=? AND conversation_id=?",
         "UPDATE amora_pg_v18_lab.conversation_runtime_sessions SET state='dormant', attention_state='disengaged', floor_owner_ref=NULL, floor_owner_entity_id=NULL, dormant_at=%s, revision=revision+1 WHERE platform=%s AND account_scope=%s AND conversation_id=%s", 4, 1),
        ("UPDATE conversation_runtime_sessions SET state='idle', attention_state='monitoring', idle_at=?, revision=revision+1 WHERE platform=? AND account_scope=? AND conversation_id=?",
         "UPDATE amora_pg_v18_lab.conversation_runtime_sessions SET state='idle', attention_state='monitoring', idle_at=%s, revision=revision+1 WHERE platform=%s AND account_scope=%s AND conversation_id=%s", 4, 1),
        ("INSERT INTO conversation_runtime_turns( turn_id, session_id, source_message_id, source_native_message_id, speaker_actor_ref, speaker_entity_id, state, revision, created_at, updated_at, reply_valid_until, cancellation_reason ) VALUES (?, ?, ?, ?, ?, ?, 'accepted', 1, ?, ?, NULL, NULL) ON CONFLICT(session_id, source_message_id) DO NOTHING",
         "INSERT INTO amora_pg_v18_lab.conversation_runtime_turns( turn_id, session_id, source_message_id, source_native_message_id, speaker_actor_ref, speaker_entity_id, state, revision, created_at, updated_at, reply_valid_until, cancellation_reason ) VALUES (%s, %s, %s, %s, %s, %s, 'accepted', 1, %s, %s, NULL, NULL) ON CONFLICT(session_id, source_message_id) DO NOTHING", 8, 1),
        ("SELECT * FROM conversation_runtime_turns WHERE session_id=? AND source_message_id=?",
         "SELECT * FROM amora_pg_v18_lab.conversation_runtime_turns WHERE session_id=%s AND source_message_id=%s", 2, 1),
        ("UPDATE conversation_runtime_turns SET state=?, revision=revision+1, updated_at=?, reply_valid_until=?, cancellation_reason=NULL WHERE turn_id=? AND state=?",
         "UPDATE amora_pg_v18_lab.conversation_runtime_turns SET state=%s, revision=revision+1, updated_at=%s, reply_valid_until=%s, cancellation_reason=NULL WHERE turn_id=%s AND state=%s", 5, 1),
        ("UPDATE conversation_runtime_turns SET state=?, revision=revision+1, updated_at=?, cancellation_reason=? WHERE turn_id=? AND state=?",
         "UPDATE amora_pg_v18_lab.conversation_runtime_turns SET state=%s, revision=revision+1, updated_at=%s, cancellation_reason=%s WHERE turn_id=%s AND state=%s", 5, 1),
        ("UPDATE conversation_runtime_turns SET state=?, revision=revision+1, updated_at=? WHERE turn_id=? AND state=?",
         "UPDATE amora_pg_v18_lab.conversation_runtime_turns SET state=%s, revision=revision+1, updated_at=%s WHERE turn_id=%s AND state=%s", 4, 1),
        ("SELECT * FROM conversation_runtime_turns WHERE turn_id=?",
         "SELECT * FROM amora_pg_v18_lab.conversation_runtime_turns WHERE turn_id=%s", 1, 1),
        (CANCEL_SOURCE,
         "UPDATE amora_pg_v18_lab.conversation_runtime_turns SET "
         "state='cancelled', revision=revision+1, updated_at=%s, "
         "cancellation_reason=%s WHERE session_id=%s "
         "AND source_native_message_id=%s "
         "AND NOT (state=ANY(%s::text[]))", 5, 1),
    )
    for key, sql, count, cap in rows:
        if key in out:
            raise ValueError("PG_CONVERSATION_RUNTIME_SQL_DUPLICATE")
        out[key]=ReviewedStatement(sql,count,cap)
    return out
