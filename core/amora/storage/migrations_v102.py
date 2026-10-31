"""People/profile identity persistence.

Person profiles contain only operator-owned stable bootstrap metadata. Learned
facts remain in World/Memory/Emotion. Platform actor identifiers are private
runtime data and are never part of the public profile catalog.
"""

MIGRATION_V102 = (
    12,
    '''
    CREATE TABLE IF NOT EXISTS person_profiles (
        entity_id TEXT PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
        profile_json TEXT NOT NULL,
        source_revision TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS platform_identity_bindings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        platform TEXT NOT NULL,
        identifier_type TEXT NOT NULL,
        external_actor_id TEXT NOT NULL,
        account_scope TEXT NOT NULL DEFAULT 'primary',
        entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
        trust TEXT NOT NULL,
        confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
        enabled INTEGER NOT NULL DEFAULT 1 CHECK(enabled IN (0,1)),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        UNIQUE(platform, identifier_type, account_scope, external_actor_id)
    );
    CREATE INDEX IF NOT EXISTS idx_platform_identity_entity
        ON platform_identity_bindings(entity_id, platform, enabled);
    CREATE INDEX IF NOT EXISTS idx_platform_identity_lookup
        ON platform_identity_bindings(platform, identifier_type, account_scope, external_actor_id, enabled);
    ''',
)

LATEST_SCHEMA_VERSION = 12
