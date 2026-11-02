from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from amora.events import AmoraEvent
from amora.identity.models import Entity, EntityRegistry, EntityType, Relationship, normalize_alias
from amora.identity.profiles import PersonProfileSeed, PlatformIdentityBinding
from amora.identity.session import DeviceIdentity, DeviceTrust, PrivacyContext, SessionIdentity

from .migrations_v101 import MIGRATION_V101
from .migrations_v102 import MIGRATION_V102
from .migrations_v103 import MIGRATION_V103
from .migrations_v104 import MIGRATION_V104
from .migrations_v105 import MIGRATION_V105
from .backend import SQLiteBackend, StorageBackend, execute_atomic_statement, is_duplicate_error
from .migrations import MIGRATIONS
from .migrations_v06 import MIGRATION_V06
from .migrations_v07 import MIGRATION_V07
from .migrations_v08 import MIGRATION_V08
from .migrations_v09 import MIGRATION_V09
from .migrations_v10 import MIGRATION_V10


class AmoraDatabase:
    def __init__(self, path: Path, *, backend: StorageBackend | None = None) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.backend = backend or SQLiteBackend()
        self._conn = self.backend.connect(self.path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def migrate(self) -> int:
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            applied = {row[0] for row in self._conn.execute("SELECT version FROM schema_migrations")}
            count = 0
            for version, sql in (*MIGRATIONS, MIGRATION_V06, MIGRATION_V07, MIGRATION_V08, MIGRATION_V09, MIGRATION_V10, MIGRATION_V101, MIGRATION_V102, MIGRATION_V103, MIGRATION_V104, MIGRATION_V105):
                if version in applied:
                    continue
                if self.backend.capabilities.remote:
                    from .remote import statements
                    with self._conn:
                        for statement in statements(sql):
                            self._conn.execute(statement)
                        self._conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
                    count += 1
                    continue
                script = f"BEGIN IMMEDIATE;\n{sql}\nINSERT INTO schema_migrations(version) VALUES ({int(version)});\nCOMMIT;"
                try:
                    self._conn.executescript(script)
                except Exception:
                    try:
                        self._conn.execute("ROLLBACK")
                    except sqlite3.OperationalError:
                        pass
                    raise
                count += 1
            return count

    def schema_version(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_migrations").fetchone()
            return int(row[0]) if row else 0

    def append_event(self, event: AmoraEvent) -> bool:
        values = (str(event.id), event.schema_version, event.event_type, event.timestamp.isoformat(), event.source, event.actor, event.target, event.confidence, event.importance, int(event.priority), json.dumps(event.payload, ensure_ascii=False, separators=(",", ":")), event.privacy_scope.value, event.session_id, str(event.correlation_id) if event.correlation_id else None, str(event.causation_id) if event.causation_id else None, str(event.causal_parent) if event.causal_parent else None, event.sequence, event.dedupe_key)
        with self._lock:
            try:
                execute_atomic_statement(
                    self.backend,
                    self._conn,
                    """INSERT INTO events (id, schema_version, event_type, timestamp, source, actor, target, confidence, importance, priority, payload_json, privacy_scope, session_id, correlation_id, causation_id, causal_parent, sequence, dedupe_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    values,
                )
                return True
            except Exception as exc:
                if is_duplicate_error(exc):
                    return False
                raise

    def get_event_by_dedupe(self, dedupe_key: str) -> AmoraEvent | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM events WHERE dedupe_key=?", (dedupe_key,)).fetchone()
        if row is None:
            return None
        return AmoraEvent.model_validate({
            "id": row["id"], "schema_version": row["schema_version"], "event_type": row["event_type"], "timestamp": row["timestamp"], "source": row["source"], "actor": row["actor"], "target": row["target"], "confidence": row["confidence"], "importance": row["importance"], "priority": row["priority"], "payload": json.loads(row["payload_json"]), "privacy_scope": row["privacy_scope"], "session_id": row["session_id"], "correlation_id": row["correlation_id"], "causation_id": row["causation_id"], "causal_parent": row["causal_parent"], "sequence": row["sequence"], "dedupe_key": row["dedupe_key"]
        })

    def save_entity(self, entity: Entity) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO entities(id, entity_type, canonical_name, metadata_json, created_at) VALUES (?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET entity_type=excluded.entity_type, canonical_name=excluded.canonical_name, metadata_json=excluded.metadata_json", (entity.id, entity.entity_type.value, entity.canonical_name, json.dumps(entity.metadata, ensure_ascii=False), entity.created_at.isoformat()))

    def get_entity(self, entity_id: str) -> Entity | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM entities WHERE id=?", (entity_id,)).fetchone()
        if row is None:
            return None
        return Entity(id=row["id"], entity_type=EntityType(row["entity_type"]), canonical_name=row["canonical_name"], metadata=json.loads(row["metadata_json"]), created_at=datetime.fromisoformat(row["created_at"]))

    def list_entities(self) -> list[Entity]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM entities ORDER BY id").fetchall()
        return [Entity(id=r["id"], entity_type=EntityType(r["entity_type"]), canonical_name=r["canonical_name"], metadata=json.loads(r["metadata_json"]), created_at=datetime.fromisoformat(r["created_at"])) for r in rows]

    def save_alias(self, alias: str, entity_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO aliases(alias_normalized, entity_id) VALUES (?, ?) ON CONFLICT(alias_normalized) DO UPDATE SET entity_id=excluded.entity_id", (normalize_alias(alias), entity_id))

    def resolve_alias(self, alias: str) -> Entity | None:
        with self._lock:
            row = self._conn.execute("SELECT entity_id FROM aliases WHERE alias_normalized=?", (normalize_alias(alias),)).fetchone()
        return self.get_entity(row["entity_id"]) if row else None

    def save_relationship(self, relationship: Relationship) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO relationships(source_id, relation_type, target_id, trust, metadata_json) VALUES (?, ?, ?, ?, ?) ON CONFLICT(source_id, relation_type, target_id) DO UPDATE SET trust=excluded.trust, metadata_json=excluded.metadata_json", (relationship.source_id, relationship.relation_type, relationship.target_id, relationship.trust, json.dumps(relationship.metadata, ensure_ascii=False)))

    def list_relationships(self) -> list[Relationship]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM relationships ORDER BY source_id, relation_type, target_id").fetchall()
        return [Relationship(source_id=r["source_id"], relation_type=r["relation_type"], target_id=r["target_id"], trust=r["trust"], metadata=json.loads(r["metadata_json"])) for r in rows]

    def load_registry(self) -> EntityRegistry:
        registry = EntityRegistry()
        for entity in self.list_entities(): registry.add(entity)
        with self._lock:
            rows = self._conn.execute("SELECT alias_normalized, entity_id FROM aliases ORDER BY alias_normalized").fetchall()
        for row in rows: registry.add_alias(row["alias_normalized"], row["entity_id"])
        for relationship in self.list_relationships(): registry.add_relationship(relationship)
        return registry

    def save_device(self, device: DeviceIdentity) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO devices(id, owner_id, label, device_type, trust, metadata_json) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET owner_id=excluded.owner_id, label=excluded.label, device_type=excluded.device_type, trust=excluded.trust, metadata_json=excluded.metadata_json", (device.id, device.owner_id, device.label, device.device_type, device.trust.value, json.dumps(device.metadata, ensure_ascii=False)))

    def list_devices(self) -> list[DeviceIdentity]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM devices ORDER BY id").fetchall()
        return [DeviceIdentity(id=r["id"], owner_id=r["owner_id"], label=r["label"], device_type=r["device_type"], trust=DeviceTrust(r["trust"]), metadata=json.loads(r["metadata_json"])) for r in rows]

    def save_session(self, session: SessionIdentity) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO sessions(id, entity_id, device_id, privacy_context, started_at, ended_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET entity_id=excluded.entity_id, device_id=excluded.device_id, privacy_context=excluded.privacy_context, started_at=excluded.started_at, ended_at=excluded.ended_at", (session.id, session.entity_id, session.device_id, session.privacy_context.value, session.started_at.isoformat(), session.ended_at.isoformat() if session.ended_at else None))

    def list_sessions(self) -> list[SessionIdentity]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM sessions ORDER BY started_at").fetchall()
        return [SessionIdentity(id=r["id"], entity_id=r["entity_id"], device_id=r["device_id"], privacy_context=PrivacyContext(r["privacy_context"]), started_at=datetime.fromisoformat(r["started_at"]), ended_at=datetime.fromisoformat(r["ended_at"]) if r["ended_at"] else None) for r in rows]

    def save_person_profile_seed(self, seed: PersonProfileSeed) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO person_profiles(entity_id, preferred_name, speech_register, relationship_label, relationship_type, relationship_trust, metadata_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(entity_id) DO UPDATE SET preferred_name=excluded.preferred_name, speech_register=excluded.speech_register, relationship_label=excluded.relationship_label, relationship_type=excluded.relationship_type, relationship_trust=excluded.relationship_trust, metadata_json=excluded.metadata_json, updated_at=excluded.updated_at", (seed.entity_id, seed.preferred_name, seed.speech_register, seed.relationship_label, seed.relationship_type, seed.relationship_trust, json.dumps(seed.metadata, ensure_ascii=False), datetime.utcnow().isoformat()))

    def get_person_profile_seed(self, entity_id: str) -> PersonProfileSeed | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM person_profiles WHERE entity_id=?", (entity_id,)).fetchone()
        if row is None: return None
        return PersonProfileSeed(entity_id=row["entity_id"], preferred_name=row["preferred_name"], speech_register=row["speech_register"], relationship_label=row["relationship_label"], relationship_type=row["relationship_type"], relationship_trust=float(row["relationship_trust"]), metadata=json.loads(row["metadata_json"]))

    def list_person_profile_seeds(self) -> list[PersonProfileSeed]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM person_profiles ORDER BY entity_id").fetchall()
        return [PersonProfileSeed(entity_id=r["entity_id"], preferred_name=r["preferred_name"], speech_register=r["speech_register"], relationship_label=r["relationship_label"], relationship_type=r["relationship_type"], relationship_trust=float(r["relationship_trust"]), metadata=json.loads(r["metadata_json"])) for r in rows]

    def save_platform_identity_binding(self, binding: PlatformIdentityBinding) -> None:
        with self._lock, self._conn:
            self._conn.execute("INSERT INTO platform_identity_bindings(platform, account_scope, identifier_type, external_actor_id, entity_id, username, display_name, aliases_json, active, metadata_json, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(platform, account_scope, identifier_type, external_actor_id) DO UPDATE SET entity_id=excluded.entity_id, username=excluded.username, display_name=excluded.display_name, aliases_json=excluded.aliases_json, active=excluded.active, metadata_json=excluded.metadata_json, updated_at=excluded.updated_at", (binding.platform, binding.account_scope, binding.identifier_type, binding.external_actor_id, binding.entity_id, binding.username, binding.display_name, json.dumps(binding.aliases, ensure_ascii=False), int(binding.active), json.dumps(binding.metadata, ensure_ascii=False), datetime.utcnow().isoformat()))

    def resolve_platform_identity_binding(self, *, platform: str, account_scope: str, identifier_type: str, external_actor_id: str) -> PlatformIdentityBinding | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM platform_identity_bindings WHERE platform=? AND account_scope=? AND identifier_type=? AND external_actor_id=? AND active=1", (platform, account_scope, identifier_type, external_actor_id)).fetchone()
        if row is None: return None
        return PlatformIdentityBinding(platform=row["platform"], account_scope=row["account_scope"], identifier_type=row["identifier_type"], external_actor_id=row["external_actor_id"], entity_id=row["entity_id"], username=row["username"], display_name=row["display_name"], aliases=json.loads(row["aliases_json"]), active=bool(row["active"]), metadata=json.loads(row["metadata_json"]))

    def list_platform_identity_bindings(self, *, platform: str | None = None, account_scope: str | None = None) -> list[PlatformIdentityBinding]:
        sql = "SELECT * FROM platform_identity_bindings"; params: list[str] = []; clauses: list[str] = []
        if platform is not None: clauses.append("platform=?"); params.append(platform)
        if account_scope is not None: clauses.append("account_scope=?"); params.append(account_scope)
        if clauses: sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY platform, account_scope, identifier_type, external_actor_id"
        with self._lock: rows = self._conn.execute(sql, tuple(params)).fetchall()
        return [PlatformIdentityBinding(platform=r["platform"], account_scope=r["account_scope"], identifier_type=r["identifier_type"], external_actor_id=r["external_actor_id"], entity_id=r["entity_id"], username=r["username"], display_name=r["display_name"], aliases=json.loads(r["aliases_json"]), active=bool(r["active"]), metadata=json.loads(r["metadata_json"])) for r in rows]
