from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path

from amora.events import AmoraEvent
from amora.identity.models import Entity, EntityRegistry, EntityType, Relationship, normalize_alias
from amora.identity.session import DeviceIdentity, DeviceTrust, PrivacyContext, SessionIdentity

from .migrations import MIGRATIONS
from .migrations_v06 import MIGRATION_V06


class AmoraDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def migrate(self) -> int:
        with self._lock:
            self._conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            applied = {row[0] for row in self._conn.execute("SELECT version FROM schema_migrations")}
            count = 0
            for version, sql in (*MIGRATIONS, MIGRATION_V06):
                if version in applied:
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
                with self._conn:
                    self._conn.execute("""INSERT INTO events (id, schema_version, event_type, timestamp, source, actor, target, confidence, importance, priority, payload_json, privacy_scope, session_id, correlation_id, causation_id, causal_parent, sequence, dedupe_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""", values)
            except sqlite3.IntegrityError:
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
        registry = EntityRegistry()
        for row in entity_rows:
            canonical_norm = normalize_alias(row["canonical_name"])
            entity_aliases = {a for a in aliases.get(row["id"], set()) if a != canonical_norm}
            registry.add_entity(Entity(id=row["id"], canonical_name=row["canonical_name"], entity_type=EntityType(row["entity_type"]), aliases=entity_aliases))
        for row in relationship_rows:
            registry.add_relationship(Relationship(source=row["source"], relation_type=row["relation_type"], target=row["target"], confidence=row["confidence"]))
        return registry

    def upsert_device(self, device: DeviceIdentity) -> None:
        with self._lock, self._conn:
            self._conn.execute("""INSERT INTO devices(id, owner_person_id, device_type, trust_level, credential_ref, capabilities_json, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET owner_person_id=excluded.owner_person_id, device_type=excluded.device_type, trust_level=excluded.trust_level, credential_ref=excluded.credential_ref, capabilities_json=excluded.capabilities_json, last_seen=excluded.last_seen""", (device.id, device.owner_person_id, device.device_type, device.trust_level.value, device.credential_ref, json.dumps(sorted(device.capabilities)), device.last_seen.isoformat()))

    def get_device(self, device_id: str) -> DeviceIdentity | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM devices WHERE id = ?", (device_id,)).fetchone()
        if row is None:
            return None
        return DeviceIdentity(id=row["id"], owner_person_id=row["owner_person_id"], device_type=row["device_type"], trust_level=DeviceTrust(row["trust_level"]), credential_ref=row["credential_ref"], capabilities=set(json.loads(row["capabilities_json"])), last_seen=datetime.fromisoformat(row["last_seen"]))

    def save_session(self, session: SessionIdentity) -> None:
        with self._lock, self._conn:
            self._conn.execute("""INSERT INTO sessions(id, device_id, authenticated_person_id, privacy_context, auth_method, started_at, last_seen, expires_at, capabilities_json, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen, expires_at=excluded.expires_at, privacy_context=excluded.privacy_context, capabilities_json=excluded.capabilities_json, closed_at=excluded.closed_at""", (session.id, session.device_id, session.authenticated_person_id, session.privacy_context.value, session.auth_method, session.started_at.isoformat(), session.last_seen.isoformat(), session.expires_at.isoformat() if session.expires_at else None, json.dumps(sorted(session.capabilities)), session.closed_at.isoformat() if session.closed_at else None))

    def get_session(self, session_id: str) -> SessionIdentity | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        return SessionIdentity(id=row["id"], device_id=row["device_id"], authenticated_person_id=row["authenticated_person_id"], privacy_context=PrivacyContext(row["privacy_context"]), auth_method=row["auth_method"], started_at=datetime.fromisoformat(row["started_at"]), last_seen=datetime.fromisoformat(row["last_seen"]), expires_at=datetime.fromisoformat(row["expires_at"]) if row["expires_at"] else None, capabilities=set(json.loads(row["capabilities_json"])), closed_at=datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None)

    def close_session(self, session_id: str, closed_at: datetime) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute("UPDATE sessions SET closed_at = ?, last_seen = ? WHERE id = ? AND closed_at IS NULL", (closed_at.isoformat(), closed_at.isoformat(), session_id))
            return cur.rowcount > 0

    def active_session_count(self, now: datetime) -> int:
        current = now.isoformat()
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM sessions WHERE closed_at IS NULL AND (expires_at IS NULL OR expires_at > ?)", (current,)).fetchone()
            return int(row[0]) if row else 0

    def integrity_check(self) -> str:
        with self._lock:
            row = self._conn.execute("PRAGMA integrity_check").fetchone()
            return str(row[0]) if row else "unknown"

    def backup_to(self, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            with self._lock:
                self._conn.backup(target)
        finally:
            target.close()
        return destination

    @staticmethod
    def restore_backup(source: Path, destination: Path) -> Path:
        if not source.is_file():
            raise FileNotFoundError(source)
        destination.parent.mkdir(parents=True, exist_ok=True)
        source_conn = sqlite3.connect(f"file:{source}?mode=ro", uri=True)
        try:
            row = source_conn.execute("PRAGMA integrity_check").fetchone()
            if row is None or row[0] != "ok":
                raise ValueError("backup integrity check failed")
            target = sqlite3.connect(destination)
            try:
                source_conn.backup(target)
            finally:
                target.close()
        finally:
            source_conn.close()
        return destination
