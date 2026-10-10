from __future__ import annotations

MIGRATION_V10: tuple[int, str] = (
    10,
    """
    CREATE TABLE IF NOT EXISTS conversation_messages (
        id TEXT PRIMARY KEY,
        turn_id TEXT NOT NULL,
        speaker_id TEXT,
        addressee_id TEXT,
        source TEXT NOT NULL,
        raw_text TEXT,
        normalized_text TEXT,
        spoken_text TEXT,
        privacy_scope TEXT NOT NULL,
        remote_processing_allowed INTEGER NOT NULL DEFAULT 0 CHECK(remote_processing_allowed IN (0,1)),
        stt_confidence REAL CHECK(stt_confidence IS NULL OR (stt_confidence >= 0 AND stt_confidence <= 1)),
        provider TEXT,
        model TEXT,
        status TEXT NOT NULL,
        interrupted INTEGER NOT NULL DEFAULT 0 CHECK(interrupted IN (0,1)),
        source_event_id TEXT,
        session_id TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        dedupe_key TEXT UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_conversation_messages_turn ON conversation_messages(turn_id, started_at);
    CREATE INDEX IF NOT EXISTS idx_conversation_messages_speaker ON conversation_messages(speaker_id, started_at DESC);
    CREATE INDEX IF NOT EXISTS idx_conversation_messages_time ON conversation_messages(started_at DESC);

    CREATE TABLE IF NOT EXISTS cognitive_journal (
        id TEXT PRIMARY KEY,
        turn_id TEXT NOT NULL,
        stage TEXT NOT NULL,
        timestamp TEXT NOT NULL,
        source TEXT NOT NULL,
        summary TEXT,
        reason_codes_json TEXT NOT NULL DEFAULT '[]',
        payload_json TEXT NOT NULL DEFAULT '{}',
        privacy_scope TEXT NOT NULL,
        provider TEXT,
        model TEXT,
        duration_ms REAL,
        correlation_id TEXT,
        error_code TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        dedupe_key TEXT UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_cognitive_journal_turn ON cognitive_journal(turn_id, timestamp);
    CREATE INDEX IF NOT EXISTS idx_cognitive_journal_stage ON cognitive_journal(stage, timestamp DESC);
    CREATE INDEX IF NOT EXISTS idx_cognitive_journal_provider ON cognitive_journal(provider, model, timestamp DESC);

    CREATE TABLE IF NOT EXISTS speech_segments (
        id TEXT PRIMARY KEY,
        turn_id TEXT NOT NULL,
        ordinal INTEGER NOT NULL,
        display_text TEXT NOT NULL,
        speech_text TEXT NOT NULL,
        status TEXT NOT NULL,
        tts_provider TEXT,
        voice_id TEXT,
        queued_at TEXT NOT NULL,
        started_at TEXT,
        finished_at TEXT,
        interrupted INTEGER NOT NULL DEFAULT 0 CHECK(interrupted IN (0,1)),
        metadata_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(turn_id, ordinal)
    );
    CREATE INDEX IF NOT EXISTS idx_speech_segments_turn ON speech_segments(turn_id, ordinal);
    CREATE INDEX IF NOT EXISTS idx_speech_segments_status ON speech_segments(status, queued_at);

    CREATE TABLE IF NOT EXISTS model_usage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp TEXT NOT NULL,
        provider TEXT NOT NULL,
        model TEXT NOT NULL,
        task_class TEXT NOT NULL,
        success INTEGER NOT NULL CHECK(success IN (0,1)),
        input_tokens INTEGER,
        output_tokens INTEGER,
        latency_ms REAL,
        status_code INTEGER,
        rate_limit_remaining_requests INTEGER,
        rate_limit_remaining_tokens INTEGER,
        turn_id TEXT,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_model_usage_provider_time ON model_usage(provider, model, timestamp DESC);
    CREATE INDEX IF NOT EXISTS idx_model_usage_turn ON model_usage(turn_id, timestamp);
    """,
)
