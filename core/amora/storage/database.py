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
            for version, sql in (*MIGRATIONS, MIGRATION_V06, MIGRATION_V07, MIGRATION_V08, MIGRATION_V09, MIGRATION_V10, MIGRATION_V101, MIGRATION_V102, MIGRATION_V103, MIGRATION_V104):
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
            except sqlite3.IntegrityError as exc:
                if not is_duplicate_error(exc):
                    raise
                return False
            return True

    def event_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM events").fetchone()
        return int(row[0]) if row else 0

    def save_registry(self, registry: EntityRegistry) -> None:
        with self._lock, self._conn:
            for entity in registry.entities:
                self._conn.execute("""INSERT INTO entities(id, canonical_name, entity_type, payload_json) VALUES (?, ?, ?, '{}') ON CONFLICT(id) DO UPDATE SET canonical_name=excluded.canonical_name, entity_type=excluded.entity_type""", (entity.id, entity.canonical_name, entity.entity_type.value))
                self._conn.execute("DELETE FROM aliases WHERE entity_id = ?", (entity.id,))
                for alias in set(entity.aliases) | {entity.canonical_name}:
                    self._conn.execute("INSERT INTO aliases(normalized_alias, entity_id) VALUES (?, ?)", (normalize_alias(alias), entity.id))
            for rel in registry.relationships:
                self._conn.execute("""INSERT INTO relationships(source, relation_type, target, confidence) VALUES (?, ?, ?, ?) ON CONFLICT(source, relation_type, target) DO UPDATE SET confidence=excluded.confidence""", (rel.source, rel.relation_type, rel.target, rel.confidence))

    def load_registry(self) -> EntityRegistry | None:
        with self._lock:
            entity_rows = self._conn.execute("SELECT id, canonical_name, entity_type FROM entities ORDER BY id").fetchall()
            if not entity_rows:
                return None
            aliases: dict[str, set[str]] = {}
            for row in self._conn.execute("SELECT normalized_alias, entity_id FROM aliases"):
                aliases.setdefault(row["entity_id"], set()).add(row["normalized_alias"])
            relationship_rows = self._conn.execute("SELECT source, relation_type, target, confidence FROM relationships ORDER BY id").fetchall()
        entities = [
            Entity(
                id=row["id"],
                canonical_name=row["canonical_name"],
                entity_type=EntityType(row["entity_type"]),
                aliases=aliases.get(row["id"], set()),
            )
            for row in entity_rows
        ]
        relationships = [
            Relationship(
                source=row["source"],
                relation_type=row["relation_type"],
                target=row["target"],
                confidence=row["confidence"],
            )
            for row in relationship_rows
        ]
        return EntityRegistry(entities=entities, relationships=relationships)

    def save_device(self, device: DeviceIdentity) -> None:
        with self._lock, self._conn:
            self._conn.execute("""INSERT INTO devices(id, owner_person_id, device_type, trust_level, credential_ref, capabilities_json, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET owner_person_id=excluded.owner_person_id, device_type=excluded.device_type, trust_level=excluded.trust_level, credential_ref=excluded.credential_ref, capabilities_json=excluded.capabilities_json, last_seen=excluded.last_seen""", (device.id, device.owner_person_id, device.device_type, device.trust_level.value, device.credential_ref, json.dumps(sorted(device.capabilities)), device.last_seen.isoformat()))

    def get_device(self, device_id: str) -> DeviceIdentity | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM devices WHERE id=?", (device_id,)).fetchone()
        if row is None:
            return None
        return DeviceIdentity(id=row["id"], owner_person_id=row["owner_person_id"], device_type=row["device_type"], trust_level=DeviceTrust(row["trust_level"]), credential_ref=row["credential_ref"], capabilities=set(json.loads(row["capabilities_json"])), last_seen=datetime.fromisoformat(row["last_seen"]))

    def save_session(self, session: SessionIdentity) -> None:
        with self._lock, self._conn:
            self._conn.execute("""INSERT INTO sessions(id, device_id, authenticated_person_id, privacy_context, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET device_id=excluded.device_id, authenticated_person_id=excluded.authenticated_person_id, privacy_context=excluded.privacy_context, created_at=excluded.created_at, expires_at=excluded.expires_at""", (session.id, session.device_id, session.authenticated_person_id, session.privacy_context.value, session.created_at.isoformat(), session.expires_at.isoformat()))

    def get_session(self, session_id: str) -> SessionIdentity | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id=?", (session_id,)).fetchone()
        if row is None:
            return None
        return SessionIdentity(id=row["id"], device_id=row["device_id"], authenticated_person_id=row["authenticated_person_id"], privacy_context=PrivacyContext(row["privacy_context"]), created_at=datetime.fromisoformat(row["created_at"]), expires_at=datetime.fromisoformat(row["expires_at"]))

    def save_person_profile_seed(self, seed: PersonProfileSeed) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO person_profile_seeds(
                       entity_id, display_name, role, relationship_to_amora, trust_level,
                       aliases_json, notes, bootstrap_source, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(entity_id) DO UPDATE SET
                       display_name=excluded.display_name,
                       role=excluded.role,
                       relationship_to_amora=excluded.relationship_to_amora,
                       trust_level=excluded.trust_level,
                       aliases_json=excluded.aliases_json,
                       notes=excluded.notes,
                       bootstrap_source=excluded.bootstrap_source,
                       updated_at=excluded.updated_at""",
                (
                    seed.entity_id,
                    seed.display_name,
                    seed.role,
                    seed.relationship_to_amora,
                    seed.trust_level,
                    json.dumps(seed.aliases, ensure_ascii=False, separators=(",", ":")),
                    seed.notes,
                    seed.bootstrap_source,
                    seed.updated_at.isoformat(),
                ),
            )

    def get_person_profile_seed(self, entity_id: str) -> PersonProfileSeed | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM person_profile_seeds WHERE entity_id=?", (entity_id,)
            ).fetchone()
        if row is None:
            return None
        return PersonProfileSeed(
            entity_id=row["entity_id"],
            display_name=row["display_name"],
            role=row["role"],
            relationship_to_amora=row["relationship_to_amora"],
            trust_level=float(row["trust_level"]),
            aliases=list(json.loads(row["aliases_json"] or "[]")),
            notes=row["notes"],
            bootstrap_source=row["bootstrap_source"],
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_person_profile_seeds(self) -> tuple[PersonProfileSeed, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM person_profile_seeds ORDER BY display_name COLLATE NOCASE, entity_id"
            ).fetchall()
        return tuple(
            PersonProfileSeed(
                entity_id=row["entity_id"],
                display_name=row["display_name"],
                role=row["role"],
                relationship_to_amora=row["relationship_to_amora"],
                trust_level=float(row["trust_level"]),
                aliases=list(json.loads(row["aliases_json"] or "[]")),
                notes=row["notes"],
                bootstrap_source=row["bootstrap_source"],
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        )

    def save_platform_identity_binding(self, binding: PlatformIdentityBinding) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """INSERT INTO platform_identity_bindings(
                       entity_id, platform, identifier_type, external_actor_id, account_scope,
                       confidence, primary_binding, active, source, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(platform, identifier_type, external_actor_id, account_scope)
                   DO UPDATE SET
                       entity_id=excluded.entity_id,
                       confidence=excluded.confidence,
                       primary_binding=excluded.primary_binding,
                       active=excluded.active,
                       source=excluded.source,
                       updated_at=excluded.updated_at""",
                (
                    binding.entity_id,
                    binding.platform,
                    binding.identifier_type,
                    binding.external_actor_id,
                    binding.account_scope,
                    binding.confidence,
                    int(binding.primary),
                    int(binding.active),
                    binding.source,
                    binding.created_at.isoformat(),
                    binding.updated_at.isoformat(),
                ),
            )

    def get_platform_identity_binding(
        self,
        *,
        platform: str,
        identifier_type: str,
        external_actor_id: str,
        account_scope: str,
    ) -> PlatformIdentityBinding | None:
        with self._lock:
            row = self._conn.execute(
                """SELECT * FROM platform_identity_bindings
                   WHERE platform=? AND identifier_type=? AND external_actor_id=?
                     AND account_scope=? AND active=1 LIMIT 1""",
                (platform, identifier_type, external_actor_id, account_scope),
            ).fetchone()
        if row is None:
            return None
        return PlatformIdentityBinding(
            entity_id=row["entity_id"],
            platform=row["platform"],
            identifier_type=row["identifier_type"],
            external_actor_id=row["external_actor_id"],
            account_scope=row["account_scope"],
            confidence=float(row["confidence"]),
            primary=bool(row["primary_binding"]),
            active=bool(row["active"]),
            source=row["source"],
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
        )

    def list_platform_identity_bindings(self, *, entity_id: str | None = None) -> tuple[PlatformIdentityBinding, ...]:
        sql = "SELECT * FROM platform_identity_bindings"
        params: tuple[object, ...] = ()
        if entity_id is not None:
            sql += " WHERE entity_id=?"
            params = (entity_id,)
        sql += " ORDER BY platform, account_scope, primary_binding DESC, updated_at DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return tuple(
            PlatformIdentityBinding(
                entity_id=row["entity_id"],
                platform=row["platform"],
                identifier_type=row["identifier_type"],
                external_actor_id=row["external_actor_id"],
                account_scope=row["account_scope"],
                confidence=float(row["confidence"]),
                primary=bool(row["primary_binding"]),
                active=bool(row["active"]),
                source=row["source"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        )

    def delete_platform_identity_binding(
        self,
        *,
        platform: str,
        identifier_type: str,
        external_actor_id: str,
        account_scope: str,
    ) -> bool:
        with self._lock, self._conn:
            cursor = self._conn.execute(
                """DELETE FROM platform_identity_bindings
                   WHERE platform=? AND identifier_type=? AND external_actor_id=? AND account_scope=?""",
                (platform, identifier_type, external_actor_id, account_scope),
            )
        return cursor.rowcount > 0

    def list_platform_identity_bindings_for_scope(
        self,
        *,
        platform: str,
        account_scope: str,
    ) -> tuple[PlatformIdentityBinding, ...]:
        with self._lock:
            rows = self._conn.execute(
                """SELECT * FROM platform_identity_bindings
                   WHERE platform=? AND account_scope=? AND active=1
                   ORDER BY primary_binding DESC, confidence DESC, updated_at DESC""",
                (platform, account_scope),
            ).fetchall()
        return tuple(
            PlatformIdentityBinding(
                entity_id=row["entity_id"],
                platform=row["platform"],
                identifier_type=row["identifier_type"],
                external_actor_id=row["external_actor_id"],
                account_scope=row["account_scope"],
                confidence=float(row["confidence"]),
                primary=bool(row["primary_binding"]),
                active=bool(row["active"]),
                source=row["source"],
                created_at=datetime.fromisoformat(row["created_at"]),
                updated_at=datetime.fromisoformat(row["updated_at"]),
            )
            for row in rows
        )
