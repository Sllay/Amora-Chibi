"""Durable bounded cognitive reflection queue for platform messaging."""

MIGRATION_V108 = (
    18,
    '''
    CREATE TABLE IF NOT EXISTS messaging_reflection_jobs (
        turn_id TEXT PRIMARY KEY,
        request_json TEXT,
        status TEXT NOT NULL DEFAULT 'pending'
            CHECK(status IN ('pending','running','retry','completed','failed')),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
        next_attempt_at TEXT NOT NULL,
        claim_token TEXT,
        claimed_until TEXT,
        last_error_code TEXT,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_messaging_reflection_pending
        ON messaging_reflection_jobs(status, next_attempt_at, claimed_until, created_at);
    ''',
)

LATEST_SCHEMA_VERSION = 18
