from __future__ import annotations


MIGRATION_V06: tuple[int, str] = (
    6,
    """
    CREATE TABLE IF NOT EXISTS autonomy_drives (
        drive TEXT PRIMARY KEY CHECK(drive IN ('social','curiosity','play','novelty','expression','competence','rest')),
        need REAL NOT NULL CHECK(need >= 0 AND need <= 1),
        baseline REAL NOT NULL CHECK(baseline >= 0 AND baseline <= 1),
        growth_per_hour REAL NOT NULL CHECK(growth_per_hour >= 0 AND growth_per_hour <= 1),
        updated_at TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS drive_adjustments (
        id TEXT PRIMARY KEY,
        drive TEXT NOT NULL CHECK(drive IN ('social','curiosity','play','novelty','expression','competence','rest')),
        delta REAL NOT NULL CHECK(delta >= -1 AND delta <= 1),
        reason TEXT NOT NULL,
        source_event_id TEXT,
        created_at TEXT NOT NULL,
        dedupe_key TEXT UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_drive_adjustments_drive_time ON drive_adjustments(drive, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_drive_adjustments_source_event ON drive_adjustments(source_event_id);

    CREATE TABLE IF NOT EXISTS autonomy_intentions (
        id TEXT PRIMARY KEY,
        kind TEXT NOT NULL CHECK(kind IN ('social_bid','initiate_contact','observe','ask_question','play','seek_novelty','express','practice','rest')),
        target_id TEXT,
        source_kind TEXT NOT NULL,
        source_ref_id TEXT,
        selected_utility REAL NOT NULL CHECK(selected_utility >= -1 AND selected_utility <= 1),
        urgency REAL NOT NULL CHECK(urgency >= 0 AND urgency <= 1),
        commitment_strength REAL NOT NULL CHECK(commitment_strength >= 0 AND commitment_strength <= 1),
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        expires_at TEXT,
        status TEXT NOT NULL CHECK(status IN ('active','completed','cancelled','expired')),
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );
    CREATE INDEX IF NOT EXISTS idx_autonomy_intention_status ON autonomy_intentions(status, expires_at, created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_autonomy_intention_source ON autonomy_intentions(source_kind, source_ref_id, status);

    CREATE TABLE IF NOT EXISTS autonomy_decisions (
        id TEXT PRIMARY KEY,
        outcome TEXT NOT NULL CHECK(outcome IN ('selected','kept_current','deferred','silence')),
        selected_intention_id TEXT REFERENCES autonomy_intentions(id) ON DELETE SET NULL,
        previous_intention_id TEXT REFERENCES autonomy_intentions(id) ON DELETE SET NULL,
        selected_utility REAL CHECK(selected_utility IS NULL OR (selected_utility >= -1 AND selected_utility <= 1)),
        silence_utility REAL NOT NULL CHECK(silence_utility >= 0 AND silence_utility <= 1),
        reason_codes_json TEXT NOT NULL DEFAULT '[]',
        result_json TEXT NOT NULL,
        created_at TEXT NOT NULL,
        dedupe_key TEXT UNIQUE
    );
    CREATE INDEX IF NOT EXISTS idx_autonomy_decisions_time ON autonomy_decisions(created_at DESC);
    CREATE INDEX IF NOT EXISTS idx_autonomy_decisions_outcome ON autonomy_decisions(outcome, created_at DESC);
    """,
)
