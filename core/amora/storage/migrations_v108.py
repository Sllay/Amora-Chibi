"""Durable platform memory reflection queue."""
MIGRATION_V108 = (
    18,
    """
    CREATE TABLE IF NOT EXISTS memory_reflection_jobs (
        turn_id TEXT PRIMARY KEY,
        input_id TEXT NOT NULL REFERENCES conversation_messages(id) ON DELETE CASCADE,
        status TEXT NOT NULL CHECK(status IN ('pending','claimed','done','failed','cancelled')),
        attempts INTEGER NOT NULL DEFAULT 0 CHECK(attempts >= 0),
        created_at TEXT NOT NULL,
        available_at TEXT NOT NULL,
        lease_token TEXT,
        lease_until TEXT,
        last_reason TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_memory_reflection_due
        ON memory_reflection_jobs(status, available_at, lease_until);
    CREATE TABLE IF NOT EXISTS memory_reflection_invalidations (
        session_id TEXT NOT NULL,
        native_message_hash TEXT NOT NULL,
        invalidated_through INTEGER NOT NULL,
        PRIMARY KEY(session_id, native_message_hash)
    );
    CREATE TABLE IF NOT EXISTS messaging_inbound_generation (
        turn_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        native_message_hash TEXT NOT NULL,
        transport_revision INTEGER NOT NULL,
        lease_token TEXT,
        lease_until TEXT,
        prepared_outbound_json TEXT,
        prepared_decision_json TEXT,
        prepared_reasons_json TEXT
    );
    CREATE INDEX IF NOT EXISTS idx_messaging_generation_source
        ON messaging_inbound_generation(session_id,native_message_hash,transport_revision);
    """,
)
