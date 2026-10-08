from __future__ import annotations

MIGRATIONS: tuple[tuple[int, str], ...] = (
    (
        1,
        """
        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            schema_version INTEGER NOT NULL,
            event_type TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            source TEXT NOT NULL,
            actor TEXT,
            target TEXT,
            confidence REAL NOT NULL,
            importance REAL NOT NULL,
            priority INTEGER NOT NULL,
            payload_json TEXT NOT NULL,
            privacy_scope TEXT NOT NULL,
            session_id TEXT,
            correlation_id TEXT,
            causation_id TEXT,
            causal_parent TEXT,
            sequence INTEGER,
            dedupe_key TEXT UNIQUE
        );
        CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, timestamp);
        CREATE INDEX IF NOT EXISTS idx_events_actor_time ON events(actor, timestamp);
        CREATE TABLE IF NOT EXISTS entities (
            id TEXT PRIMARY KEY,
            canonical_name TEXT NOT NULL,
            entity_type TEXT NOT NULL,
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
        """,
    ),
    (
        2,
        """
        CREATE TABLE IF NOT EXISTS devices (
            id TEXT PRIMARY KEY,
            owner_person_id TEXT REFERENCES entities(id),
            device_type TEXT NOT NULL,
            trust_level TEXT NOT NULL,
            credential_ref TEXT,
            capabilities_json TEXT NOT NULL DEFAULT '[]',
            last_seen TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id TEXT PRIMARY KEY,
            device_id TEXT NOT NULL REFERENCES devices(id),
            authenticated_person_id TEXT REFERENCES entities(id),
            privacy_context TEXT NOT NULL,
            auth_method TEXT NOT NULL,
            started_at TEXT NOT NULL,
            last_seen TEXT NOT NULL,
            expires_at TEXT,
            capabilities_json TEXT NOT NULL DEFAULT '[]',
            closed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_sessions_device_active ON sessions(device_id, closed_at);
        CREATE INDEX IF NOT EXISTS idx_sessions_person_active ON sessions(authenticated_person_id, closed_at);
        """,
    ),
    (
        3,
        """
        CREATE TABLE IF NOT EXISTS memory_records (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK(kind IN ('episodic', 'semantic', 'relational')),
            content TEXT NOT NULL,
            summary TEXT,
            provenance_kind TEXT NOT NULL,
            provenance_source TEXT NOT NULL,
            provenance_confidence REAL NOT NULL CHECK(provenance_confidence >= 0 AND provenance_confidence <= 1),
            source_event_id TEXT REFERENCES events(id) ON DELETE SET NULL,
            actor_id TEXT,
            session_id TEXT,
            derived_from_json TEXT NOT NULL DEFAULT '[]',
            privacy_scope TEXT NOT NULL,
            owner_entity_id TEXT,
            audience_json TEXT NOT NULL DEFAULT '[]',
            remote_processing_allowed INTEGER NOT NULL DEFAULT 1 CHECK(remote_processing_allowed IN (0,1)),
            valid_from TEXT,
            valid_until TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            importance REAL NOT NULL CHECK(importance >= 0 AND importance <= 1),
            gate_score REAL NOT NULL CHECK(gate_score >= 0 AND gate_score <= 1),
            retrievability REAL NOT NULL DEFAULT 1 CHECK(retrievability >= 0 AND retrievability <= 1),
            access_count INTEGER NOT NULL DEFAULT 0 CHECK(access_count >= 0),
            last_accessed_at TEXT,
            archived_at TEXT,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_memory_kind_created ON memory_records(kind, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_memory_source_event ON memory_records(source_event_id);
        CREATE INDEX IF NOT EXISTS idx_memory_validity ON memory_records(valid_from, valid_until);
        CREATE INDEX IF NOT EXISTS idx_memory_retrievability ON memory_records(archived_at, retrievability DESC, importance DESC);

        CREATE TABLE IF NOT EXISTS memory_entities (
            memory_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
            entity_id TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            PRIMARY KEY(memory_id, entity_id)
        );
        CREATE INDEX IF NOT EXISTS idx_memory_entities_entity ON memory_entities(entity_id, confidence DESC);

        CREATE TABLE IF NOT EXISTS memory_tags (
            memory_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
            tag TEXT NOT NULL,
            PRIMARY KEY(memory_id, tag)
        );
        CREATE INDEX IF NOT EXISTS idx_memory_tags_tag ON memory_tags(tag);

        CREATE TABLE IF NOT EXISTS memory_links (
            source_memory_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
            target_memory_id TEXT NOT NULL REFERENCES memory_records(id) ON DELETE CASCADE,
            link_type TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            PRIMARY KEY(source_memory_id, target_memory_id, link_type),
            CHECK(source_memory_id <> target_memory_id)
        );
        CREATE INDEX IF NOT EXISTS idx_memory_links_target ON memory_links(target_memory_id, link_type);
        """,
    ),
)
