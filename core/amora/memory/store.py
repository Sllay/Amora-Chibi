from __future__ import annotations

import json
import re
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Iterable
from uuid import UUID

from amora.identity import EntityRegistry
from amora.storage.backend import SQLiteBackend, StorageBackend
from amora.time import Clock

from .models import MemoryHit, MemoryKind, MemoryProvenance, MemoryQuery, MemoryRecord, ProvenanceKind


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)
_RRF_K = 60.0


def _tokens(text: str) -> set[str]:
    return {token.casefold() for token in _TOKEN_RE.findall(text) if len(token) > 1}


def _jaccard(a: str, b: str) -> float:
    left, right = _tokens(a), _tokens(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


class MemoryStore:
    """SQLite-backed long-term memory store with hybrid local retrieval."""

    def __init__(self, path: Path, clock: Clock, identities: EntityRegistry, *, backend: StorageBackend | None = None) -> None:
        self.path = path
        self.clock = clock
        self.identities = identities
        self._lock = threading.RLock()
        self.backend = backend or SQLiteBackend()
        self._conn = self.backend.connect(path)
        self.fts_enabled = self._ensure_fts()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _ensure_fts(self) -> bool:
        try:
            with self._lock, self._conn:
                self._conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
                        memory_id UNINDEXED,
                        content,
                        tags,
                        tokenize='unicode61 remove_diacritics 2'
                    )
                    """
                )
            return True
        except sqlite3.OperationalError:
            return False

    def count(self, *, include_archived: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM memory_records"
        if not include_archived:
            sql += " WHERE archived_at IS NULL"
        with self._lock:
            row = self._conn.execute(sql).fetchone()
        return int(row[0]) if row else 0

    def put(self, record: MemoryRecord) -> None:
        provenance = record.provenance
        values = (
            str(record.id), record.kind.value, record.content, record.summary,
            provenance.kind.value, provenance.source, provenance.confidence,
            str(provenance.source_event_id) if provenance.source_event_id else None,
            provenance.actor_id, provenance.session_id,
            json.dumps([str(item) for item in provenance.derived_from], separators=(",", ":")),
            record.privacy_scope.value, record.owner_entity_id,
            json.dumps(sorted(record.audience), ensure_ascii=False, separators=(",", ":")),
            1 if record.remote_processing_allowed else 0,
            record.valid_from.isoformat() if record.valid_from else None,
            record.valid_until.isoformat() if record.valid_until else None,
            record.created_at.isoformat(), record.updated_at.isoformat(), record.importance,
            record.gate_score, record.retrievability, record.access_count,
            record.last_accessed_at.isoformat() if record.last_accessed_at else None,
            record.archived_at.isoformat() if record.archived_at else None,
            json.dumps(record.metadata, ensure_ascii=False, separators=(",", ":")),
        )
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO memory_records(
                    id, kind, content, summary, provenance_kind, provenance_source,
                    provenance_confidence, source_event_id, actor_id, session_id,
                    derived_from_json, privacy_scope, owner_entity_id, audience_json,
                    remote_processing_allowed, valid_from, valid_until, created_at, updated_at,
                    importance, gate_score, retrievability, access_count, last_accessed_at,
                    archived_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    kind=excluded.kind, content=excluded.content, summary=excluded.summary,
                    provenance_kind=excluded.provenance_kind,
                    provenance_source=excluded.provenance_source,
                    provenance_confidence=excluded.provenance_confidence,
                    source_event_id=excluded.source_event_id, actor_id=excluded.actor_id,
                    session_id=excluded.session_id, derived_from_json=excluded.derived_from_json,
                    privacy_scope=excluded.privacy_scope, owner_entity_id=excluded.owner_entity_id,
                    audience_json=excluded.audience_json,
                    remote_processing_allowed=excluded.remote_processing_allowed,
                    valid_from=excluded.valid_from, valid_until=excluded.valid_until,
                    updated_at=excluded.updated_at, importance=excluded.importance,
                    gate_score=excluded.gate_score, retrievability=excluded.retrievability,
                    access_count=excluded.access_count, last_accessed_at=excluded.last_accessed_at,
                    archived_at=excluded.archived_at, metadata_json=excluded.metadata_json
                """,
                values,
            )
            self._conn.execute("DELETE FROM memory_entities WHERE memory_id = ?", (str(record.id),))
            self._conn.executemany(
                "INSERT INTO memory_entities(memory_id, entity_id, confidence) VALUES (?, ?, ?)",
                [(str(record.id), entity_id, confidence) for entity_id, confidence in sorted(record.entity_refs.items())],
            )
            self._conn.execute("DELETE FROM memory_tags WHERE memory_id = ?", (str(record.id),))
            self._conn.executemany(
                "INSERT INTO memory_tags(memory_id, tag) VALUES (?, ?)",
                [(str(record.id), tag) for tag in sorted(record.tags)],
            )
            if self.fts_enabled:
                self._conn.execute("DELETE FROM memory_fts WHERE memory_id = ?", (str(record.id),))
                self._conn.execute(
                    "INSERT INTO memory_fts(memory_id, content, tags) VALUES (?, ?, ?)",
                    (str(record.id), " ".join(filter(None, (record.content, record.summary or ""))), " ".join(sorted(record.tags))),
                )

    def get(self, memory_id: UUID | str) -> MemoryRecord | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM memory_records WHERE id = ?", (str(memory_id),)).fetchone()
            if row is None:
                return None
            return self._hydrate(row)

    def link(self, source_memory_id: UUID | str, target_memory_id: UUID | str, link_type: str, confidence: float = 1.0) -> None:
        confidence = max(0.0, min(1.0, float(confidence)))
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO memory_links(source_memory_id, target_memory_id, link_type, confidence)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(source_memory_id, target_memory_id, link_type)
                DO UPDATE SET confidence=excluded.confidence
                """,
                (str(source_memory_id), str(target_memory_id), link_type, confidence),
            )

    def degrade_retrievability(self, memory_id: UUID | str, factor: float) -> float | None:
        factor = max(0.0, min(1.0, float(factor)))
        with self._lock, self._conn:
            row = self._conn.execute("SELECT retrievability FROM memory_records WHERE id = ?", (str(memory_id),)).fetchone()
            if row is None:
                return None
            value = max(0.02, min(1.0, float(row[0]) * factor))
            self._conn.execute(
                "UPDATE memory_records SET retrievability = ?, updated_at = ? WHERE id = ?",
                (value, self.clock.now().isoformat(), str(memory_id)),
            )
            return value

    def archive(self, memory_id: UUID | str) -> bool:
        now = self.clock.now().isoformat()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE memory_records SET archived_at = ?, updated_at = ? WHERE id = ? AND archived_at IS NULL",
                (now, now, str(memory_id)),
            )
            return cur.rowcount > 0

    def search(self, query: MemoryQuery) -> list[MemoryHit]:
        rows = self._candidate_rows(query)
        if not rows:
            return []

        records = {row["id"]: self._hydrate(row) for row in rows}
        scores: dict[str, float] = {memory_id: 0.0 for memory_id in records}
        reasons: dict[str, set[str]] = {memory_id: set() for memory_id in records}

        text_rank = self._text_rank(query.text, records)
        self._add_rrf(scores, reasons, text_rank, 3.0, "TEXT_MATCH")
        entity_rank, related_rank = self._entity_ranks(query.entity_ids, records.keys())
        self._add_rrf(scores, reasons, entity_rank, 2.2, "ENTITY_MATCH")
        self._add_rrf(scores, reasons, related_rank, 1.2, "RELATION_MATCH")
        recency_rank = sorted(records, key=lambda memory_id: records[memory_id].created_at, reverse=True)
        self._add_rrf(scores, reasons, recency_rank, 0.8, "RECENCY")
        importance_rank = sorted(
            records,
            key=lambda memory_id: records[memory_id].importance * records[memory_id].retrievability,
            reverse=True,
        )
        self._add_rrf(scores, reasons, importance_rank, 1.0, "IMPORTANCE_RETRIEVABILITY")

        if query.text and not text_rank:
            return []

        ranked = sorted(scores, key=scores.get, reverse=True)
        selected = self._mmr_select(ranked, records, scores, query.limit, query.mmr_lambda)
        max_score = max((scores[memory_id] for memory_id in selected), default=1.0) or 1.0
        now = self.clock.now()
        hits = [
            MemoryHit(
                memory=records[memory_id],
                score=round(scores[memory_id] / max_score, 6),
                reason_codes=sorted(reasons[memory_id]),
            )
            for memory_id in selected
        ]
        self._mark_accessed(selected, now)
        for hit in hits:
            hit.memory.access_count += 1
            hit.memory.last_accessed_at = now
        return hits

    def _candidate_rows(self, query: MemoryQuery) -> list[sqlite3.Row]:
        where: list[str] = []
        params: list[object] = []
        now = self.clock.now().isoformat()
        if not query.include_archived:
            where.append("archived_at IS NULL")
        where.append("(valid_until IS NULL OR valid_until >= ?)")
        params.append(now)
        if query.kinds:
            placeholders = ",".join("?" for _ in query.kinds)
            where.append(f"kind IN ({placeholders})")
            params.extend(sorted(kind.value for kind in query.kinds))
        if query.after:
            where.append("created_at >= ?")
            params.append(query.after.isoformat())
        if query.before:
            where.append("created_at <= ?")
            params.append(query.before.isoformat())

        sql = "SELECT * FROM memory_records"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY (importance * retrievability) DESC, created_at DESC LIMIT 300"
        with self._lock:
            return list(self._conn.execute(sql, params).fetchall())

    def _text_rank(self, text: str, records: dict[str, MemoryRecord]) -> list[str]:
        query_tokens = list(_tokens(text))[:16]
        if not query_tokens:
            return []
        if self.fts_enabled:
            expression = " OR ".join(f'"{token}"' for token in query_tokens)
            with self._lock:
                rows = self._conn.execute(
                    "SELECT memory_id, bm25(memory_fts) AS rank FROM memory_fts WHERE memory_fts MATCH ? ORDER BY rank LIMIT 150",
                    (expression,),
                ).fetchall()
            ids = [row["memory_id"] for row in rows if row["memory_id"] in records]
            if ids:
                return ids
        query_set = set(query_tokens)
        scored: list[tuple[float, str]] = []
        for memory_id, record in records.items():
            haystack = _tokens(" ".join((record.content, record.summary or "", " ".join(record.tags))))
            if not haystack:
                continue
            overlap = len(query_set & haystack)
            if overlap:
                scored.append((overlap / len(query_set | haystack), memory_id))
        scored.sort(reverse=True)
        return [memory_id for _, memory_id in scored]

    def _entity_ranks(self, entity_ids: set[str], candidate_ids: Iterable[str]) -> tuple[list[str], list[str]]:
        if not entity_ids:
            return [], []
        candidate_set = set(candidate_ids)
        placeholders = ",".join("?" for _ in entity_ids)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT memory_id, entity_id, confidence FROM memory_entities WHERE entity_id IN ({placeholders}) ORDER BY confidence DESC",
                sorted(entity_ids),
            ).fetchall()
        exact = [row["memory_id"] for row in rows if row["memory_id"] in candidate_set]

        related_entities: set[str] = set()
        for rel in self.identities.relationships:
            if rel.source in entity_ids:
                related_entities.add(rel.target)
            if rel.target in entity_ids:
                related_entities.add(rel.source)
        related_entities -= entity_ids
        related: list[str] = []
        if related_entities:
            placeholders = ",".join("?" for _ in related_entities)
            with self._lock:
                rows = self._conn.execute(
                    f"SELECT memory_id, confidence FROM memory_entities WHERE entity_id IN ({placeholders}) ORDER BY confidence DESC",
                    sorted(related_entities),
                ).fetchall()
            seen = set(exact)
            related = [row["memory_id"] for row in rows if row["memory_id"] in candidate_set and row["memory_id"] not in seen]
        return list(dict.fromkeys(exact)), list(dict.fromkeys(related))

    @staticmethod
    def _add_rrf(scores: dict[str, float], reasons: dict[str, set[str]], ranking: list[str], weight: float, reason: str) -> None:
        for rank, memory_id in enumerate(ranking, start=1):
            if memory_id not in scores:
                continue
            scores[memory_id] += weight / (_RRF_K + rank)
            reasons[memory_id].add(reason)

    @staticmethod
    def _mmr_select(ranked: list[str], records: dict[str, MemoryRecord], scores: dict[str, float], limit: int, mmr_lambda: float) -> list[str]:
        if not ranked:
            return []
        max_relevance = max(scores.values()) or 1.0
        remaining = list(ranked)
        selected: list[str] = []
        while remaining and len(selected) < limit:
            best_id: str | None = None
            best_score = float("-inf")
            for memory_id in remaining:
                relevance = scores[memory_id] / max_relevance
                redundancy = 0.0
                if selected:
                    redundancy = max(_jaccard(records[memory_id].content, records[other_id].content) for other_id in selected)
                mmr = mmr_lambda * relevance - (1.0 - mmr_lambda) * redundancy
                if mmr > best_score:
                    best_score = mmr
                    best_id = memory_id
            assert best_id is not None
            selected.append(best_id)
            remaining.remove(best_id)
        return selected

    def _mark_accessed(self, memory_ids: list[str], now: datetime) -> None:
        if not memory_ids:
            return
        with self._lock, self._conn:
            self._conn.executemany(
                "UPDATE memory_records SET access_count = access_count + 1, last_accessed_at = ? WHERE id = ?",
                [(now.isoformat(), memory_id) for memory_id in memory_ids],
            )

    def _hydrate(self, row: sqlite3.Row) -> MemoryRecord:
        memory_id = row["id"]
        with self._lock:
            entity_rows = self._conn.execute(
                "SELECT entity_id, confidence FROM memory_entities WHERE memory_id = ? ORDER BY entity_id",
                (memory_id,),
            ).fetchall()
            tag_rows = self._conn.execute(
                "SELECT tag FROM memory_tags WHERE memory_id = ? ORDER BY tag",
                (memory_id,),
            ).fetchall()
        provenance = MemoryProvenance(
            kind=ProvenanceKind(row["provenance_kind"]),
            source=row["provenance_source"],
            confidence=float(row["provenance_confidence"]),
            source_event_id=row["source_event_id"],
            actor_id=row["actor_id"],
            session_id=row["session_id"],
            derived_from=json.loads(row["derived_from_json"]),
        )
        return MemoryRecord(
            id=memory_id,
            kind=MemoryKind(row["kind"]),
            content=row["content"],
            summary=row["summary"],
            provenance=provenance,
            privacy_scope=row["privacy_scope"],
            owner_entity_id=row["owner_entity_id"],
            audience=set(json.loads(row["audience_json"])),
            remote_processing_allowed=bool(row["remote_processing_allowed"]),
            entity_refs={item["entity_id"]: float(item["confidence"]) for item in entity_rows},
            tags={item["tag"] for item in tag_rows},
            valid_from=datetime.fromisoformat(row["valid_from"]) if row["valid_from"] else None,
            valid_until=datetime.fromisoformat(row["valid_until"]) if row["valid_until"] else None,
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            importance=float(row["importance"]),
            gate_score=float(row["gate_score"]),
            retrievability=float(row["retrievability"]),
            access_count=int(row["access_count"]),
            last_accessed_at=datetime.fromisoformat(row["last_accessed_at"]) if row["last_accessed_at"] else None,
            archived_at=datetime.fromisoformat(row["archived_at"]) if row["archived_at"] else None,
            metadata=json.loads(row["metadata_json"]),
        )
