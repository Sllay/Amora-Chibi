from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from amora.events import AmoraEvent
from .migrations import MIGRATIONS


class AmoraDatabase:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")

    def close(self) -> None:
        self._conn.close()

    def migrate(self) -> int:
        self._conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
        applied = {row[0] for row in self._conn.execute("SELECT version FROM schema_migrations")}
        count = 0
        for version, sql in MIGRATIONS:
            if version in applied:
                continue
            with self._conn:
                self._conn.executescript(sql)
                self._conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (version,))
            count += 1
        return count

    def append_event(self, event: AmoraEvent) -> bool:
        values = (str(event.id), event.schema_version, event.event_type, event.timestamp.isoformat(), event.source, event.actor, event.target, event.confidence, event.importance, int(event.priority), json.dumps(event.payload, ensure_ascii=False, separators=(",", ":")), event.privacy_scope.value, event.session_id, str(event.correlation_id) if event.correlation_id else None, str(event.causation_id) if event.causation_id else None, str(event.causal_parent) if event.causal_parent else None, event.sequence, event.dedupe_key)
        try:
            with self._conn:
                self._conn.execute("INSERT INTO events (id, schema_version, event_type, timestamp, source, actor, target, confidence, importance, priority, payload_json, privacy_scope, session_id, correlation_id, causation_id, causal_parent, sequence, dedupe_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", values)
        except sqlite3.IntegrityError:
            return False
        return True

    def integrity_check(self) -> str:
        row = self._conn.execute("PRAGMA integrity_check").fetchone()
        return str(row[0]) if row else "unknown"
