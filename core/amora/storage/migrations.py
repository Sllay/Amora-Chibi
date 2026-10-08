from __future__ import annotations

MIGRATIONS: tuple[tuple[int, str], ...] = ((1, """
CREATE TABLE IF NOT EXISTS events (
 id TEXT PRIMARY KEY, schema_version INTEGER NOT NULL, event_type TEXT NOT NULL,
 timestamp TEXT NOT NULL, source TEXT NOT NULL, actor TEXT, target TEXT,
 confidence REAL NOT NULL, importance REAL NOT NULL, priority INTEGER NOT NULL,
 payload_json TEXT NOT NULL, privacy_scope TEXT NOT NULL, session_id TEXT,
 correlation_id TEXT, causation_id TEXT, causal_parent TEXT, sequence INTEGER,
 dedupe_key TEXT UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, timestamp);
CREATE INDEX IF NOT EXISTS idx_events_actor_time ON events(actor, timestamp);
CREATE TABLE IF NOT EXISTS entities (
 id TEXT PRIMARY KEY, canonical_name TEXT NOT NULL, entity_type TEXT NOT NULL,
 payload_json TEXT NOT NULL DEFAULT '{}'
);
CREATE TABLE IF NOT EXISTS aliases (
 normalized_alias TEXT PRIMARY KEY,
 entity_id TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS relationships (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 source TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
 relation_type TEXT NOT NULL,
 target TEXT NOT NULL REFERENCES entities(id) ON DELETE CASCADE,
 confidence REAL NOT NULL,
 UNIQUE(source, relation_type, target)
);
"""),)
