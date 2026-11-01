"""Shared conversational attention floor for stateless/multi-instance Core."""

MIGRATION_V104 = (
    14,
    '''
    CREATE TABLE IF NOT EXISTS messaging_conversation_floor (
        platform TEXT NOT NULL,
        account_scope TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        generation INTEGER NOT NULL DEFAULT 0,
        speaker_actor_ref TEXT,
        speaker_entity_id TEXT,
        opened_at TEXT,
        expires_at TEXT,
        anchor_message_id TEXT,
        response_source_message_id TEXT,
        reason TEXT,
        PRIMARY KEY(platform, account_scope, conversation_id)
    );
    CREATE INDEX IF NOT EXISTS idx_messaging_floor_expiry
        ON messaging_conversation_floor(expires_at);
    ''',
)

LATEST_SCHEMA_VERSION = 14
