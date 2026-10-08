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
    (
        4,
        """
        CREATE TABLE IF NOT EXISTS world_claims (
            id TEXT PRIMARY KEY,
            kind TEXT NOT NULL CHECK(kind IN ('observation','report','belief','inference','hypothesis')),
            statement TEXT NOT NULL,
            subject_id TEXT,
            predicate TEXT NOT NULL,
            object_json TEXT NOT NULL,
            context_key TEXT NOT NULL DEFAULT '',
            source TEXT NOT NULL,
            source_entity_id TEXT,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            valid_from TEXT NOT NULL,
            valid_until TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            canonical INTEGER NOT NULL DEFAULT 0 CHECK(canonical IN (0,1)),
            supersedes_claim_id TEXT REFERENCES world_claims(id) ON DELETE SET NULL,
            privacy_scope TEXT NOT NULL,
            remote_processing_allowed INTEGER NOT NULL DEFAULT 1 CHECK(remote_processing_allowed IN (0,1)),
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_world_claim_key ON world_claims(subject_id, predicate, context_key, valid_from DESC);
        CREATE INDEX IF NOT EXISTS idx_world_current ON world_claims(canonical, kind, valid_until, valid_from DESC);
        CREATE INDEX IF NOT EXISTS idx_world_source ON world_claims(source_entity_id, created_at DESC);

        CREATE TABLE IF NOT EXISTS world_evidence (
            claim_id TEXT NOT NULL REFERENCES world_claims(id) ON DELETE CASCADE,
            ordinal INTEGER NOT NULL,
            kind TEXT NOT NULL CHECK(kind IN ('event','memory','claim','external')),
            ref_id TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            note TEXT,
            PRIMARY KEY(claim_id, ordinal)
        );
        CREATE INDEX IF NOT EXISTS idx_world_evidence_ref ON world_evidence(kind, ref_id);

        CREATE TABLE IF NOT EXISTS self_entries (
            id TEXT PRIMARY KEY,
            layer TEXT NOT NULL CHECK(layer IN ('core_identity','traits','preferences','relationships','self_narrative','current_state')),
            key TEXT NOT NULL,
            value_json TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            source TEXT NOT NULL,
            locked INTEGER NOT NULL DEFAULT 0 CHECK(locked IN (0,1)),
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}',
            UNIQUE(layer, key)
        );
        CREATE INDEX IF NOT EXISTS idx_self_layer ON self_entries(layer, updated_at DESC);

        CREATE TABLE IF NOT EXISTS relationship_revisions (
            id TEXT PRIMARY KEY,
            source_id TEXT NOT NULL,
            relation_type TEXT NOT NULL,
            target_id TEXT NOT NULL,
            confidence REAL NOT NULL CHECK(confidence >= 0 AND confidence <= 1),
            source_of_truth TEXT NOT NULL,
            valid_from TEXT NOT NULL,
            valid_until TEXT,
            created_at TEXT NOT NULL,
            supersedes_id TEXT REFERENCES relationship_revisions(id) ON DELETE SET NULL,
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_relationship_revision_key ON relationship_revisions(source_id, relation_type, target_id, valid_from DESC);
        CREATE INDEX IF NOT EXISTS idx_relationship_revision_active ON relationship_revisions(source_id, target_id, valid_until);
        """,
    ),
    (
        5,
        """
        CREATE TABLE IF NOT EXISTS emotion_events (
            id TEXT PRIMARY KEY,
            emotion TEXT NOT NULL CHECK(emotion IN ('joy','gratitude','relief','anger','irritation','fear','disappointment')),
            intensity REAL NOT NULL CHECK(intensity >= 0 AND intensity <= 1),
            cause_event_id TEXT,
            cause_description TEXT NOT NULL,
            target_id TEXT,
            agent_id TEXT,
            blame REAL NOT NULL CHECK(blame >= 0 AND blame <= 1),
            intentionality REAL NOT NULL CHECK(intentionality >= 0 AND intentionality <= 1),
            spillover REAL NOT NULL CHECK(spillover >= 0 AND spillover <= 1),
            appraisal_json TEXT NOT NULL,
            privacy_scope TEXT NOT NULL,
            created_at TEXT NOT NULL,
            dedupe_key TEXT UNIQUE
        );
        CREATE INDEX IF NOT EXISTS idx_emotion_target_time ON emotion_events(target_id, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_emotion_type_time ON emotion_events(emotion, created_at DESC);
        CREATE INDEX IF NOT EXISTS idx_emotion_cause ON emotion_events(cause_event_id);

        CREATE TABLE IF NOT EXISTS mood_state (
            singleton_id INTEGER PRIMARY KEY CHECK(singleton_id = 1),
            valence REAL NOT NULL CHECK(valence >= -1 AND valence <= 1),
            arousal REAL NOT NULL CHECK(arousal >= 0 AND arousal <= 1),
            dominance REAL NOT NULL CHECK(dominance >= -1 AND dominance <= 1),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS entity_affect (
            entity_id TEXT PRIMARY KEY REFERENCES entities(id) ON DELETE CASCADE,
            anger REAL NOT NULL DEFAULT 0 CHECK(anger >= 0 AND anger <= 1),
            affection REAL NOT NULL DEFAULT 0 CHECK(affection >= 0 AND affection <= 1),
            trust REAL NOT NULL DEFAULT 0.5 CHECK(trust >= 0 AND trust <= 1),
            irritation REAL NOT NULL DEFAULT 0 CHECK(irritation >= 0 AND irritation <= 1),
            fear REAL NOT NULL DEFAULT 0 CHECK(fear >= 0 AND fear <= 1),
            gratitude REAL NOT NULL DEFAULT 0 CHECK(gratitude >= 0 AND gratitude <= 1),
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS social_bids (
            id TEXT PRIMARY KEY,
            bid_type TEXT NOT NULL CHECK(bid_type IN ('express_gratitude','seek_contact','seek_repair','set_boundary','withdraw')),
            target_id TEXT,
            strength REAL NOT NULL CHECK(strength >= 0 AND strength <= 1),
            reason TEXT NOT NULL,
            source_emotion_event_id TEXT NOT NULL REFERENCES emotion_events(id) ON DELETE CASCADE,
            created_at TEXT NOT NULL,
            expires_at TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('pending','consumed','cancelled','expired')),
            metadata_json TEXT NOT NULL DEFAULT '{}'
        );
        CREATE INDEX IF NOT EXISTS idx_social_bids_pending ON social_bids(status, expires_at, strength DESC);
        CREATE INDEX IF NOT EXISTS idx_social_bids_target ON social_bids(target_id, created_at DESC);
        """,
    ),
)
