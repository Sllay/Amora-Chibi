"""Persistent perceptual/social memory for transport expressions."""

MIGRATION_V106 = (
    16,
    '''
    CREATE TABLE IF NOT EXISTS perceptual_expression_assets (
        platform TEXT NOT NULL,
        account_scope TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        native_id TEXT NOT NULL,
        name TEXT,
        description TEXT,
        tags_json TEXT NOT NULL DEFAULT '[]',
        semantic_profile_json TEXT,
        semantic_status TEXT NOT NULL DEFAULT 'metadata_only'
            CHECK(semantic_status IN ('metadata_only','pending_visual','enriched','stale','failed')),
        fingerprint TEXT NOT NULL,
        fingerprint_source TEXT NOT NULL DEFAULT 'transport_metadata',
        semantic_revision INTEGER NOT NULL DEFAULT 0 CHECK(semantic_revision >= 0),
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        seen_count INTEGER NOT NULL DEFAULT 1 CHECK(seen_count >= 1),
        unique_senders_count INTEGER NOT NULL DEFAULT 0 CHECK(unique_senders_count >= 0),
        last_sender_ref TEXT,
        last_source_message_id TEXT,
        last_visual_analysis_at TEXT,
        last_visual_analysis_fingerprint TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        PRIMARY KEY(platform, account_scope, scope_id, kind, native_id)
    );
    CREATE INDEX IF NOT EXISTS idx_perceptual_expression_last_seen
        ON perceptual_expression_assets(platform, account_scope, scope_id, last_seen_at DESC);
    CREATE INDEX IF NOT EXISTS idx_perceptual_expression_status
        ON perceptual_expression_assets(semantic_status, last_seen_at DESC);

    CREATE TABLE IF NOT EXISTS perceptual_expression_senders (
        platform TEXT NOT NULL,
        account_scope TEXT NOT NULL,
        scope_id TEXT NOT NULL,
        kind TEXT NOT NULL,
        native_id TEXT NOT NULL,
        sender_ref TEXT NOT NULL,
        first_seen_at TEXT NOT NULL,
        last_seen_at TEXT NOT NULL,
        seen_count INTEGER NOT NULL DEFAULT 1 CHECK(seen_count >= 1),
        PRIMARY KEY(platform, account_scope, scope_id, kind, native_id, sender_ref),
        FOREIGN KEY(platform, account_scope, scope_id, kind, native_id)
            REFERENCES perceptual_expression_assets(platform, account_scope, scope_id, kind, native_id)
            ON DELETE CASCADE
    );
    CREATE INDEX IF NOT EXISTS idx_perceptual_expression_sender
        ON perceptual_expression_senders(sender_ref, last_seen_at DESC);
    ''',
)

LATEST_SCHEMA_VERSION = 16
