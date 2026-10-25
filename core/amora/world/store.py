from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from uuid import UUID

from amora.events import PrivacyScope
from amora.storage.backend import SQLiteBackend, StorageBackend
from amora.time import Clock

from .models import (
    EpistemicKind,
    EvidenceKind,
    EvidenceRef,
    RelationshipState,
    SelfEntry,
    SelfLayer,
    WorldClaim,
    WorldQuery,
)


class WorldStore:
    """SQLite persistence for V0.4 epistemic state, self model and relation history."""

    def __init__(self, path: Path, clock: Clock, *, backend: StorageBackend | None = None) -> None:
        self.path = path
        self.clock = clock
        self._lock = threading.RLock()
        self.backend = backend or SQLiteBackend()
        self._conn = self.backend.connect(path)

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def claim_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM world_claims").fetchone()
        return int(row[0]) if row else 0

    def put_claim(self, claim: WorldClaim) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO world_claims(
                    id, kind, statement, subject_id, predicate, object_json, context_key,
                    source, source_entity_id, confidence, valid_from, valid_until,
                    created_at, updated_at, canonical, supersedes_claim_id, privacy_scope,
                    remote_processing_allowed, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(claim.id), claim.kind.value, claim.statement, claim.subject_id,
                    claim.predicate, json.dumps(claim.object_value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    claim.context_key, claim.source, claim.source_entity_id, claim.confidence,
                    claim.valid_from.isoformat(), claim.valid_until.isoformat() if claim.valid_until else None,
                    claim.created_at.isoformat(), claim.updated_at.isoformat(), 1 if claim.canonical else 0,
                    str(claim.supersedes_claim_id) if claim.supersedes_claim_id else None,
                    claim.privacy_scope.value, 1 if claim.remote_processing_allowed else 0,
                    json.dumps(claim.metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )
            self._conn.executemany(
                """
                INSERT INTO world_evidence(claim_id, ordinal, kind, ref_id, confidence, note)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (str(claim.id), index, evidence.kind.value, evidence.ref_id, evidence.confidence, evidence.note)
                    for index, evidence in enumerate(claim.evidence)
                ],
            )

    def get_claim(self, claim_id: UUID | str) -> WorldClaim | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM world_claims WHERE id = ?", (str(claim_id),)).fetchone()
            if row is None:
                return None
            return self._hydrate_claim(row)

    def expire_claim(self, claim_id: UUID | str, when: datetime) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                UPDATE world_claims
                SET valid_until = ?, updated_at = ?
                WHERE id = ? AND (valid_until IS NULL OR valid_until > ?)
                """,
                (when.isoformat(), when.isoformat(), str(claim_id), when.isoformat()),
            )
            return cur.rowcount > 0

    def active_belief(
        self,
        *,
        subject_id: str | None,
        predicate: str,
        context_key: str = "",
        at: datetime | None = None,
    ) -> WorldClaim | None:
        instant = (at or self.clock.now()).isoformat()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM world_claims
                WHERE kind = 'belief' AND canonical = 1
                  AND subject_id IS ? AND predicate = ? AND context_key = ?
                  AND valid_from <= ?
                  AND (valid_until IS NULL OR valid_until > ?)
                ORDER BY valid_from DESC, created_at DESC
                LIMIT 1
                """,
                (subject_id, predicate, context_key, instant, instant),
            ).fetchone()
            return self._hydrate_claim(row) if row else None

    def query_claims(self, query: WorldQuery, *, at: datetime | None = None) -> list[WorldClaim]:
        clauses: list[str] = []
        params: list[object] = []
        if query.subject_id is not None:
            clauses.append("subject_id = ?")
            params.append(query.subject_id)
        if query.predicate is not None:
            clauses.append("predicate = ?")
            params.append(query.predicate)
        if query.context_key is not None:
            clauses.append("context_key = ?")
            params.append(query.context_key)
        if query.kinds:
            placeholders = ",".join("?" for _ in query.kinds)
            clauses.append(f"kind IN ({placeholders})")
            params.extend(sorted(kind.value for kind in query.kinds))
        if query.canonical_only:
            clauses.append("canonical = 1")
        if not query.include_expired:
            instant = (at or self.clock.now()).isoformat()
            clauses.append("valid_from <= ?")
            params.append(instant)
            clauses.append("(valid_until IS NULL OR valid_until > ?)")
            params.append(instant)
        sql = "SELECT * FROM world_claims"
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY valid_from DESC, created_at DESC LIMIT ?"
        params.append(query.limit)
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
            return [self._hydrate_claim(row) for row in rows]

    def current_state(self, *, subject_id: str | None = None, at: datetime | None = None) -> list[WorldClaim]:
        return self.query_claims(
            WorldQuery(
                subject_id=subject_id,
                kinds={EpistemicKind.BELIEF},
                canonical_only=True,
                include_expired=False,
                limit=500,
            ),
            at=at,
        )

    def upsert_self_entry(self, entry: SelfEntry) -> SelfEntry:
        with self._lock, self._conn:
            row = self._conn.execute(
                "SELECT id, created_at FROM self_entries WHERE layer = ? AND key = ?",
                (entry.layer.value, entry.key),
            ).fetchone()
            if row:
                entry.id = UUID(row["id"])
                entry.created_at = datetime.fromisoformat(row["created_at"])
            self._conn.execute(
                """
                INSERT INTO self_entries(id, layer, key, value_json, confidence, source, locked, created_at, updated_at, metadata_json)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(layer, key) DO UPDATE SET
                    value_json=excluded.value_json,
                    confidence=excluded.confidence,
                    source=excluded.source,
                    locked=excluded.locked,
                    updated_at=excluded.updated_at,
                    metadata_json=excluded.metadata_json
                """,
                (
                    str(entry.id), entry.layer.value, entry.key,
                    json.dumps(entry.value, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                    entry.confidence, entry.source, 1 if entry.locked else 0,
                    entry.created_at.isoformat(), entry.updated_at.isoformat(),
                    json.dumps(entry.metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )
        return entry

    def get_self_entry(self, layer: SelfLayer, key: str) -> SelfEntry | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM self_entries WHERE layer = ? AND key = ?",
                (layer.value, key),
            ).fetchone()
        return self._hydrate_self(row) if row else None

    def list_self_entries(self, layer: SelfLayer | None = None) -> list[SelfEntry]:
        sql = "SELECT * FROM self_entries"
        params: tuple[object, ...] = ()
        if layer is not None:
            sql += " WHERE layer = ?"
            params = (layer.value,)
        sql += " ORDER BY layer, key"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._hydrate_self(row) for row in rows]

    def put_relationship(self, relation: RelationshipState) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO relationship_revisions(
                    id, source_id, relation_type, target_id, confidence, source_of_truth,
                    valid_from, valid_until, created_at, supersedes_id, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(relation.id), relation.source_id, relation.relation_type, relation.target_id,
                    relation.confidence, relation.source_of_truth, relation.valid_from.isoformat(),
                    relation.valid_until.isoformat() if relation.valid_until else None,
                    relation.created_at.isoformat(), str(relation.supersedes_id) if relation.supersedes_id else None,
                    json.dumps(relation.metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
                ),
            )

    def expire_relationship(self, relation_id: UUID | str, when: datetime) -> bool:
        with self._lock, self._conn:
            cur = self._conn.execute(
                """
                UPDATE relationship_revisions SET valid_until = ?
                WHERE id = ? AND (valid_until IS NULL OR valid_until > ?)
                """,
                (when.isoformat(), str(relation_id), when.isoformat()),
            )
            return cur.rowcount > 0

    def current_relationship(self, source_id: str, relation_type: str, target_id: str, *, at: datetime | None = None) -> RelationshipState | None:
        instant = (at or self.clock.now()).isoformat()
        with self._lock:
            row = self._conn.execute(
                """
                SELECT * FROM relationship_revisions
                WHERE source_id = ? AND relation_type = ? AND target_id = ?
                  AND valid_from <= ? AND (valid_until IS NULL OR valid_until > ?)
                ORDER BY valid_from DESC, created_at DESC LIMIT 1
                """,
                (source_id, relation_type, target_id, instant, instant),
            ).fetchone()
        return self._hydrate_relationship(row) if row else None

    def relationship_history(self, source_id: str, *, target_id: str | None = None) -> list[RelationshipState]:
        sql = "SELECT * FROM relationship_revisions WHERE source_id = ?"
        params: list[object] = [source_id]
        if target_id is not None:
            sql += " AND target_id = ?"
            params.append(target_id)
        sql += " ORDER BY valid_from DESC, created_at DESC"
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [self._hydrate_relationship(row) for row in rows]

    def _hydrate_claim(self, row: sqlite3.Row) -> WorldClaim:
        with self._lock:
            evidence_rows = self._conn.execute(
                "SELECT kind, ref_id, confidence, note FROM world_evidence WHERE claim_id = ? ORDER BY ordinal",
                (row["id"],),
            ).fetchall()
        return WorldClaim(
            id=UUID(row["id"]),
            kind=EpistemicKind(row["kind"]),
            statement=row["statement"],
            subject_id=row["subject_id"],
            predicate=row["predicate"],
            object_value=json.loads(row["object_json"]),
            context_key=row["context_key"],
            source=row["source"],
            source_entity_id=row["source_entity_id"],
            confidence=float(row["confidence"]),
            evidence=[EvidenceRef(kind=EvidenceKind(item["kind"]), ref_id=item["ref_id"], confidence=float(item["confidence"]), note=item["note"]) for item in evidence_rows],
            valid_from=datetime.fromisoformat(row["valid_from"]),
            valid_until=datetime.fromisoformat(row["valid_until"]) if row["valid_until"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            canonical=bool(row["canonical"]),
            supersedes_claim_id=UUID(row["supersedes_claim_id"]) if row["supersedes_claim_id"] else None,
            privacy_scope=PrivacyScope(row["privacy_scope"]),
            remote_processing_allowed=bool(row["remote_processing_allowed"]),
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _hydrate_self(row: sqlite3.Row) -> SelfEntry:
        return SelfEntry(
            id=UUID(row["id"]),
            layer=SelfLayer(row["layer"]),
            key=row["key"],
            value=json.loads(row["value_json"]),
            confidence=float(row["confidence"]),
            source=row["source"],
            locked=bool(row["locked"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            metadata=json.loads(row["metadata_json"]),
        )

    @staticmethod
    def _hydrate_relationship(row: sqlite3.Row) -> RelationshipState:
        return RelationshipState(
            id=UUID(row["id"]),
            source_id=row["source_id"],
            relation_type=row["relation_type"],
            target_id=row["target_id"],
            confidence=float(row["confidence"]),
            source_of_truth=row["source_of_truth"],
            valid_from=datetime.fromisoformat(row["valid_from"]),
            valid_until=datetime.fromisoformat(row["valid_until"]) if row["valid_until"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            supersedes_id=UUID(row["supersedes_id"]) if row["supersedes_id"] else None,
            metadata=json.loads(row["metadata_json"]),
        )
