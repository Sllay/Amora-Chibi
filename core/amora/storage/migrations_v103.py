"""Platform messaging persistence.

Messaging audit is operational/social history, not long-term Amora memory. Raw
platform identifiers remain private runtime data and are never projected through
health/public diagnostics.
"""

MIGRATION_V103 = (
    13,
    '''
    CREATE TABLE IF NOT EXISTS messaging_audit (
        id TEXT PRIMARY KEY,
        platform TEXT NOT NULL,
        account_scope TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        message_id TEXT NOT NULL,
        direction TEXT NOT NULL CHECK(direction IN ('inbound','outbound')),
        channel_type TEXT NOT NULL CHECK(channel_type IN ('dm','group')),
        sender_external_id TEXT,
        speaker_entity_id TEXT,
        text TEXT NOT NULL DEFAULT '',
        reply_to_message_id TEXT,
        reply_to_author_entity_id TEXT,
        mentions_json TEXT NOT NULL DEFAULT '[]',
        routing_action TEXT,
        reason_codes_json TEXT NOT NULL DEFAULT '[]',
        responded INTEGER NOT NULL DEFAULT 0 CHECK(responded IN (0,1)),
        timestamp TEXT NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(platform, account_scope, conversation_id, message_id, direction)
    );
    CREATE INDEX IF NOT EXISTS idx_messaging_audit_conversation
        ON messaging_audit(platform, account_scope, conversation_id, timestamp DESC);
    CREATE INDEX IF NOT EXISTS idx_messaging_audit_speaker
        ON messaging_audit(speaker_entity_id, timestamp DESC);

    CREATE TABLE IF NOT EXISTS messaging_outbound_queue (
        id TEXT PRIMARY KEY,
        platform TEXT NOT NULL,
        account_scope TEXT NOT NULL,
        conversation_id TEXT NOT NULL,
        target_entity_id TEXT,
        text TEXT NOT NULL,
        reply_to_message_id TEXT,
        mention_actor_ids_json TEXT NOT NULL DEFAULT '[]',
        source_message_id TEXT,
        created_at TEXT NOT NULL,
        status TEXT NOT NULL CHECK(status IN ('queued','claimed','sent','failed','cancelled')),
        claimed_at TEXT,
        claim_expires_at TEXT,
        external_message_id TEXT,
        error_code TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_messaging_outbound_claim
        ON messaging_outbound_queue(platform, account_scope, status, created_at);

    CREATE TABLE IF NOT EXISTS platform_presence (
        platform TEXT NOT NULL,
        account_scope TEXT NOT NULL,
        state TEXT NOT NULL,
        desired_connected INTEGER NOT NULL DEFAULT 0 CHECK(desired_connected IN (0,1)),
        check_requested INTEGER NOT NULL DEFAULT 0 CHECK(check_requested IN (0,1)),
        generation INTEGER NOT NULL DEFAULT 0,
        updated_at TEXT NOT NULL,
        last_connected_at TEXT,
        last_disconnected_at TEXT,
        last_error_code TEXT,
        PRIMARY KEY(platform, account_scope)
    );
    ''',
)

LATEST_SCHEMA_VERSION = 13
