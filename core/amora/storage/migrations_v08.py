MIGRATION_V08 = (
    8,
    """
    CREATE TABLE IF NOT EXISTS conversation_turn_decisions (
        id TEXT PRIMARY KEY,
        state TEXT NOT NULL,
        score REAL NOT NULL,
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        dedupe_key TEXT UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_conversation_turn_state_created
        ON conversation_turn_decisions(state, created_at DESC);

    CREATE TABLE IF NOT EXISTS speech_utterances (
        id TEXT PRIMARY KEY,
        target_id TEXT,
        kind TEXT NOT NULL,
        priority INTEGER NOT NULL,
        privacy_scope TEXT NOT NULL,
        device_id TEXT NOT NULL,
        status TEXT NOT NULL,
        utterance_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        claimed_at TEXT,
        claim_expires_at TEXT,
        spoken_at TEXT,
        dedupe_key TEXT UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_speech_queue_device_status_priority
        ON speech_utterances(device_id, status, priority DESC, created_at ASC);
    CREATE INDEX IF NOT EXISTS idx_speech_claim_expiry
        ON speech_utterances(status, claim_expires_at);
    """,
)
