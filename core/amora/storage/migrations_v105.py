"""Persistent transport-agnostic conversation runtime state."""

MIGRATION_V105 = (
    15,
    '''
    CREATE TABLE IF NOT EXISTS conversation_runtime_sessions (
        platform TEXT NOT NULL,
        account_scope TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        session_id TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('active','idle','dormant','closed')),
        attention_state TEXT NOT NULL CHECK(attention_state IN ('direct','engaged','monitoring','background','disengaged')),
        floor_owner_ref TEXT,
        floor_owner_entity_id TEXT,
        opened_at TEXT NOT NULL,
        last_activity_at TEXT NOT NULL,
        idle_at TEXT,
        dormant_at TEXT,
        closed_at TEXT,
        revision INTEGER NOT NULL DEFAULT 1,
        PRIMARY KEY(platform, account_scope, conversation_id)
    );
    CREATE INDEX IF NOT EXISTS idx_conversation_runtime_activity
        ON conversation_runtime_sessions(last_activity_at);

    CREATE TABLE IF NOT EXISTS conversation_runtime_turns (
        turn_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        source_message_id TEXT NOT NULL,
        speaker_actor_ref TEXT NOT NULL,
        speaker_entity_id TEXT,
        state TEXT NOT NULL CHECK(state IN ('received','accepted','thinking','ready','delivering','delivered','cancelled','superseded','failed','recovering')),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        reply_valid_until TEXT,
        cancellation_reason TEXT,
        UNIQUE(session_id, source_message_id)
    );
    CREATE INDEX IF NOT EXISTS idx_conversation_runtime_turn_session
        ON conversation_runtime_turns(session_id, updated_at);
    ''',
)

LATEST_SCHEMA_VERSION = 15
