from __future__ import annotations

from amora.latency_trace import trace_stage, trace_lock

import json
import re
import sqlite3
import threading
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable
from uuid import UUID

from amora.identity import EntityRegistry
from amora.storage.backend import SQLiteBackend, StorageBackend, execute_atomic_statement
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
        with trace_lock(self._lock):
            self._conn.close()

    def _ensure_fts(self) -> bool:
        try:
            with trace_lock(self._lock), self._conn:
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
        except sqlite3.OperationalError as exc:
            # Connectivity, permission and disk errors must never disable retrieval silently.
            if str(exc) == "FTS5_UNSUPPORTED" or "no such module: fts5" in str(exc):
                return False
            raise

    def count(self, *, include_archived: bool = False) -> int:
        sql = "SELECT COUNT(*) FROM memory_records"
        if not include_archived:
            sql += " WHERE archived_at IS NULL"
        with trace_lock(self._lock):
            row = self._conn.execute(sql).fetchone()
        return int(row[0]) if row else 0

    def put(self, record: MemoryRecord, *, reflection_lease: tuple[str, str] | None = None,
            generation_lease: tuple[str, str] | None = None) -> None:
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
        with trace_lock(self._lock), self._conn:
            if generation_lease is not None:
                turn_id, token = generation_lease
                # Verify the persisted source and active inbound lease in the
                # write transaction; an edited/deleted source cannot be saved.
                lease = self._conn.execute(
                    """SELECT 1 FROM messaging_inbound_generation g
                       JOIN conversation_messages m ON m.turn_id=g.turn_id
                         AND m.dedupe_key='cognitive-input:' || g.turn_id
                       WHERE g.turn_id=? AND g.lease_token=? AND g.lease_until>?
                         AND m.speaker_id=? AND m.session_id=g.session_id
                         AND m.session_id=? AND m.privacy_scope=?
                         AND (?=0 OR (m.remote_processing_allowed=1 AND m.privacy_scope='PERSONAL'))
                         AND json_extract(m.metadata_json,'$.explicit_memory_request')=1
                         AND json_extract(m.metadata_json,'$.channel_type')='dm'
                         AND NOT EXISTS (SELECT 1 FROM memory_reflection_invalidations x
                           WHERE x.session_id=g.session_id
                             AND x.native_message_hash=g.native_message_hash
                             AND x.invalidated_through>=g.transport_revision)""",
                    (turn_id, token, self.clock.now().isoformat(), record.owner_entity_id,
                     provenance.session_id, record.privacy_scope.value,
                     int(record.remote_processing_allowed)),
                ).fetchone()
                if lease is None:
                    raise RuntimeError("INBOUND_GENERATION_LEASE_LOST")
            if reflection_lease is not None:
                turn_id, token = reflection_lease
                lease = self._conn.execute(
                    "SELECT 1 FROM memory_reflection_jobs WHERE turn_id=? AND status='claimed' "
                    "AND lease_token=? AND lease_until>?",
                    (turn_id, token, self.clock.now().isoformat()),
                ).fetchone()
                if lease is None:
                    raise RuntimeError("REFLECTION_LEASE_LOST")
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
        with trace_lock(self._lock):
            row = self._conn.execute("SELECT * FROM memory_records WHERE id = ?", (str(memory_id),)).fetchone()
            if row is None:
                return None
            return self._hydrate(row)

    def local_only_reflection_audit(self) -> dict:
        with trace_lock(self._lock):
            count = self._conn.execute(
                "SELECT COUNT(*) FROM memory_records WHERE archived_at IS NULL "
                "AND remote_processing_allowed=0 AND provenance_source LIKE 'cognition:%'"
            ).fetchone()[0]
            rows = self._conn.execute(
                "SELECT id,owner_entity_id,privacy_scope FROM memory_records WHERE archived_at IS NULL "
                "AND remote_processing_allowed=0 AND provenance_source LIKE 'cognition:%' "
                "ORDER BY created_at DESC LIMIT 100"
            ).fetchall()
        return {"total": int(count), "review_candidates": [dict(row) for row in rows]}

    def authorize_reviewed_reflection(self, memory_id: UUID | str, owner_id: str) -> bool:
        """Narrow, explicit owner-reviewed reuse; never public or bulk promotion.

        Original DM input must have allowed remote processing. The operator
        attests the individual memory was reviewed and its owner approved reuse.
        """
        if self.identities.get(owner_id) is None:
            return False
        with trace_lock(self._lock), self._conn:
            cursor = self._conn.execute(
                """UPDATE memory_records SET remote_processing_allowed=1,
                       owner_entity_id=?, updated_at=?
                   WHERE id=? AND privacy_scope='PERSONAL' AND archived_at IS NULL
                     AND remote_processing_allowed=0 AND provenance_source LIKE 'cognition:%'
                     AND provenance_kind='inference' AND actor_id='ENTITY_AMORA'
                     AND (owner_entity_id IS NULL OR owner_entity_id=?)
                     AND NOT EXISTS (SELECT 1 FROM json_each(audience_json) WHERE value<>?)
                     AND json_array_length(derived_from_json)=0
                     AND EXISTS (
                         SELECT 1 FROM conversation_messages m
                         WHERE m.turn_id=json_extract(memory_records.metadata_json,'$.turn_id')
                           AND m.dedupe_key='cognitive-input:' || m.turn_id
                           AND m.session_id=memory_records.session_id AND m.speaker_id=?
                           AND m.privacy_scope='PERSONAL' AND m.remote_processing_allowed=1
                           AND json_extract(m.metadata_json,'$.surface')='platform_messaging'
                           AND json_extract(m.metadata_json,'$.channel_type')='dm'
                           AND COALESCE(json_extract(m.metadata_json,'$.test_mode'),0)=0
                           AND COALESCE(json_extract(m.metadata_json,'$.memory_consolidation'),1)=1
                           AND NOT EXISTS (
                               SELECT 1 FROM memory_reflection_invalidations x
                               WHERE x.session_id=m.session_id
                                 AND x.native_message_hash=json_extract(m.metadata_json,'$.native_message_id_hash')
                                 AND x.invalidated_through>=COALESCE(json_extract(m.metadata_json,'$.transport_revision'),1)
                           )
                     )""",
                (owner_id, self.clock.now().isoformat(), str(memory_id), owner_id, owner_id, owner_id),
            )
            return cursor.rowcount == 1

    def link(self, source_memory_id: UUID | str, target_memory_id: UUID | str, link_type: str, confidence: float = 1.0) -> None:
        confidence = max(0.0, min(1.0, float(confidence)))
        with trace_lock(self._lock), self._conn:
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
        with trace_lock(self._lock), self._conn:
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
        with trace_lock(self._lock), self._conn:
            cur = self._conn.execute(
                "UPDATE memory_records SET archived_at = ?, updated_at = ? WHERE id = ? AND archived_at IS NULL",
                (now, now, str(memory_id)),
            )
            return cur.rowcount > 0

    def recall_owner_records(self, owner_id: str, topic: str, *, generic: bool = False,
                             limit: int = 3, public_only: bool = False,
                             share_only: bool = False) -> tuple[list[MemoryRecord], bool]:
        """Read-only, owner-scoped recall independent of conversation history.

        Search the full eligible FTS corpus first. A bounded fallback covers
        non-FTS backends, with explicit incomplete-search status. Never return
        unrelated entity matches for a named subject.
        """
        from .recall_intent import topic_terms
        terms = topic_terms(topic)
        if not owner_id or (not generic and not terms):
            return [], True
        limit = max(1, min(5, limit))
        now = self.clock.now().isoformat()
        if share_only:
            from .disclosure import SHAREABLE_MEMORY_SQL
            sql = "SELECT m.* FROM memory_records m WHERE " + SHAREABLE_MEMORY_SQL
        else:
            sql = ("SELECT * FROM memory_records WHERE owner_entity_id=? "
               "AND privacy_scope IN ('PERSONAL','PRIVATE','SOCIAL','PUBLIC') "
               "AND archived_at IS NULL AND (valid_from IS NULL OR valid_from<=?) "
               "AND (valid_until IS NULL OR valid_until>=?)")
        args = (owner_id, now, now)
        if public_only:
            sql += " AND privacy_scope='PUBLIC'"
        order = " ORDER BY created_at DESC,id DESC"
        def matches(row):
            return generic or terms <= topic_terms(
                str(row["content"]) + " " + str(row["summary"] or ""))
        with trace_lock(self._lock):
            if generic:
                rows = self._conn.execute(sql + order + " LIMIT ?", (*args, limit)).fetchall()
                return list(self._hydrate_many(list(rows)).values()), True
            if self.fts_enabled:
                expression = " AND ".join(f'"{term}"' for term in sorted(terms))
                rows = self._conn.execute(
                    "SELECT m.* FROM memory_fts JOIN (" + sql + ") AS m ON m.id=memory_fts.memory_id "
                    "WHERE memory_fts MATCH ? ORDER BY m.created_at DESC,m.id DESC LIMIT 129",
                    (*args, expression),
                ).fetchall()
                matched = [row for row in rows[:128] if matches(row)]
                if matched:
                    return list(self._hydrate_many(matched[:limit]).values()), len(rows) <= 128
            # FTS can be unavailable or incomplete for a legacy record. Use
            # bounded pages, Unicode-normalized exact terms and no broad recall.
            matched = []
            complete = False
            for page in range(8):
                rows = self._conn.execute(sql + order + " LIMIT 64 OFFSET ?", (*args, page * 64)).fetchall()
                matched.extend(row for row in rows if matches(row))
                if len(rows) < 64:
                    complete = True
                    break
                if len(matched) >= limit:
                    break
            return list(self._hydrate_many(matched[:limit]).values()), complete

    def shareable_owner_record(self, owner_id: str, record_id: str) -> MemoryRecord | None:
        from .disclosure import SHAREABLE_MEMORY_SQL
        now = self.clock.now().isoformat()
        with trace_lock(self._lock):
            row = self._conn.execute(
                "SELECT m.* FROM memory_records m WHERE " + SHAREABLE_MEMORY_SQL + " AND m.id=?",
                (owner_id, now, now, record_id),
            ).fetchone()
            return self._hydrate(row) if row is not None else None

    def owner_turn_memory_status(self, turn_id: str, owner_id: str) -> list[dict]:
        """Operator-only metadata; never return the memory text or other owners."""
        with trace_lock(self._lock):
            rows = self._conn.execute(
                """SELECT id,privacy_scope,remote_processing_allowed,archived_at,
                          provenance_kind,provenance_source,valid_from,valid_until
                   FROM memory_records WHERE owner_entity_id=?
                     AND privacy_scope NOT IN ('AUTH','SECRET')
                     AND json_extract(metadata_json,'$.turn_id')=?
                   ORDER BY created_at,id LIMIT 50""",
                (owner_id, str(turn_id)),
            ).fetchall()
        now = self.clock.now().isoformat()
        return [{
            "memory_id": str(row["id"]), "privacy_scope": row["privacy_scope"],
            "remote_processing_allowed": bool(row["remote_processing_allowed"]),
            "active": row["archived_at"] is None and
                (row["valid_from"] is None or row["valid_from"] <= now) and
                (row["valid_until"] is None or row["valid_until"] >= now),
            "archived": row["archived_at"] is not None,
            "provenance_kind": row["provenance_kind"],
            "provenance_source": row["provenance_source"],
        } for row in rows]

    @trace_stage("memory_search")
    def search(self, query: MemoryQuery) -> list[MemoryHit]:
        rows = self._candidate_rows(query)
        if not rows:
            return []

        # Candidate hydration used to issue two SQL round-trips per row (entities
        # and tags). That is cheap in local SQLite but catastrophic over Hrana.
        # Preserve the exact candidate/ranking semantics while loading both child
        # collections in two bounded bulk reads.
        records = self._hydrate_many(rows)
        # Apply projection permissions before scoring/MMR, not after top-k.
        if query.projection_recipient_id or query.projection_public_only or query.projection_remote_only:
            from amora.events import PrivacyScope
            def eligible(record):
                if record.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
                    return False
                if query.projection_remote_only and not record.remote_processing_allowed:
                    return False
                if record.privacy_scope == PrivacyScope.PUBLIC:
                    return True
                if query.projection_public_only or not query.projection_recipient_id:
                    return False
                recipient = query.projection_recipient_id
                audience = set(record.audience)
                if record.owner_entity_id == recipient or recipient in audience:
                    return True
                if record.owner_entity_id or audience:
                    return False
                return record.provenance.actor_id == recipient
            records = {key: record for key, record in records.items() if eligible(record)}
            if not records:
                return []
        if query.require_topic_match:
            from .recall_intent import topic_terms
            terms = topic_terms(query.text)
            records = {key: record for key, record in records.items()
                       if terms and terms <= topic_terms(record.content + " " + (record.summary or ""))}
            if not records:
                return []
        scores: dict[str, float] = {memory_id: 0.0 for memory_id in records}
        reasons: dict[str, set[str]] = {memory_id: set() for memory_id in records}

        text_rank = self._text_rank(query.text, records)
        if query.require_topic_match and not text_rank:
            # Unicode-normalized matching above already proved the topic.
            text_rank = sorted(records, key=lambda key: records[key].created_at, reverse=True)
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
            if not query.allow_entity_fallback or not entity_rank:
                return []
            # Explicit recollection requests can recover directly linked memories
            # without pretending that a lexical match established the requested fact.
            direct = set(entity_rank)
            ranked = sorted(direct, key=lambda key: (-scores[key], key))
            for key in ranked:
                reasons[key].add("ENTITY_RECALL_NO_TEXT_MATCH")
        else:
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

    @trace_stage("memory_candidates")
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

        if query.projection_public_only:
            where.append("privacy_scope = ?")
            params.append("PUBLIC")
        if query.projection_remote_only:
            where.append("remote_processing_allowed = 1")
        if query.projection_recipient_id and not query.projection_public_only:
            # Restrict the candidate pool before its size cap. JSON audience
            # membership is exact, not substring matching.
            where.append("privacy_scope NOT IN ('AUTH', 'SECRET')")
            where.append(
                "(privacy_scope = 'PUBLIC' OR owner_entity_id = ? OR "
                "EXISTS (SELECT 1 FROM json_each(audience_json) WHERE value = ?) OR "
                "(COALESCE(owner_entity_id, '') = '' AND "
                "json_array_length(audience_json) = 0 AND actor_id = ?))"
            )
            params.extend([query.projection_recipient_id] * 3)
        sql = "SELECT * FROM memory_records"
        if where:
            sql += " WHERE " + " AND ".join(where)
        baseline = sql + " ORDER BY (importance * retrievability) DESC, created_at DESC, id LIMIT 300"
        tokens = sorted(_tokens(query.text))[:16]
        with trace_lock(self._lock):
            rows = list(self._conn.execute(baseline, params).fetchall())
            if not tokens:
                return rows
            # The lexical candidate path searches the entire eligible corpus,
            # not merely the highest-importance 300 records.
            if self.fts_enabled:
                expression = " OR ".join(f'"{token}"' for token in tokens)
                matched = self._conn.execute(
                    "SELECT m.* FROM memory_fts JOIN (" + sql + ") AS m "
                    "ON m.id = memory_fts.memory_id WHERE memory_fts MATCH ? "
                    "ORDER BY bm25(memory_fts), m.id LIMIT 150",
                    (*params, expression),
                ).fetchall()
            else:
                # FTS is optional on some backends. Keep a bounded result set
                # while searching text and tags across the eligible corpus.
                clauses = []
                args = []
                for token in tokens:
                    clauses.append(
                        "(instr(lower(m.content), ?) > 0 OR "
                        "instr(lower(COALESCE(m.summary, '')), ?) > 0 OR "
                        "EXISTS (SELECT 1 FROM memory_tags t WHERE t.memory_id = m.id "
                        "AND instr(lower(t.tag), ?) > 0))"
                    )
                    args.extend([token] * 3)
                matched = self._conn.execute(
                    "SELECT m.* FROM (" + sql + ") AS m WHERE " + " OR ".join(clauses)
                    + " ORDER BY (m.importance * m.retrievability) DESC, m.created_at DESC, m.id LIMIT 150",
                    (*params, *args),
                ).fetchall()
            # At most 450 hydrated candidates, regardless of corpus size.
            return list({row["id"]: row for row in [*rows, *matched]}.values())

    @trace_stage("memory_text_rank")
    def _text_rank(self, text: str, records: dict[str, MemoryRecord]) -> list[str]:
        query_tokens = sorted(_tokens(text))[:16]
        if not query_tokens:
            return []
        if self.fts_enabled:
            expression = " OR ".join(f'"{token}"' for token in query_tokens)
            with trace_lock(self._lock):
                rows = self._conn.execute(
                    "SELECT memory_id, bm25(memory_fts) AS rank FROM memory_fts WHERE memory_fts MATCH ? "
                    "AND memory_id IN (" + ",".join("?" for _ in records) + ") ORDER BY rank, memory_id LIMIT 150",
                    (expression, *records),
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

    @trace_stage("memory_entity_rank")
    def _entity_ranks(self, entity_ids: set[str], candidate_ids: Iterable[str]) -> tuple[list[str], list[str]]:
        if not entity_ids:
            return [], []
        candidate_set = set(candidate_ids)
        placeholders = ",".join("?" for _ in entity_ids)
        with trace_lock(self._lock):
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
            with trace_lock(self._lock):
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

    @trace_stage("memory_access_write")
    def _mark_accessed(self, memory_ids: list[str], now: datetime) -> None:
        if not memory_ids:
            return
        placeholders = ",".join("?" for _ in memory_ids)
        with trace_lock(self._lock):
            execute_atomic_statement(
                self.backend, self._conn,
                f"UPDATE memory_records SET access_count = access_count + 1, last_accessed_at = ? WHERE id IN ({placeholders})",
                (now.isoformat(), *memory_ids),
            )

    @trace_stage("memory_hydrate")
    def _hydrate_many(self, rows: list[sqlite3.Row]) -> dict[str, MemoryRecord]:
        if not rows:
            return {}
        memory_ids = [str(row["id"]) for row in rows]
        placeholders = ",".join("?" for _ in memory_ids)
        with trace_lock(self._lock):
            entity_rows = self._conn.execute(
                f"SELECT memory_id, entity_id, confidence FROM memory_entities WHERE memory_id IN ({placeholders}) ORDER BY memory_id, entity_id",
                memory_ids,
            ).fetchall()
            tag_rows = self._conn.execute(
                f"SELECT memory_id, tag FROM memory_tags WHERE memory_id IN ({placeholders}) ORDER BY memory_id, tag",
                memory_ids,
            ).fetchall()

        entities: dict[str, list[object]] = defaultdict(list)
        tags: dict[str, list[object]] = defaultdict(list)
        for item in entity_rows:
            entities[str(item["memory_id"])].append(item)
        for item in tag_rows:
            tags[str(item["memory_id"])].append(item)
        return {
            str(row["id"]): self._hydrate(
                row,
                entity_rows=entities.get(str(row["id"]), ()),
                tag_rows=tags.get(str(row["id"]), ()),
            )
            for row in rows
        }

    def _hydrate(
        self,
        row: sqlite3.Row,
        *,
        entity_rows: Iterable[object] | None = None,
        tag_rows: Iterable[object] | None = None,
    ) -> MemoryRecord:
        memory_id = row["id"]
        if entity_rows is None or tag_rows is None:
            with trace_lock(self._lock):
                loaded_entities = self._conn.execute(
                    "SELECT entity_id, confidence FROM memory_entities WHERE memory_id = ? ORDER BY entity_id",
                    (memory_id,),
                ).fetchall()
                loaded_tags = self._conn.execute(
                    "SELECT tag FROM memory_tags WHERE memory_id = ? ORDER BY tag",
                    (memory_id,),
                ).fetchall()
            if entity_rows is None:
                entity_rows = loaded_entities
            if tag_rows is None:
                tag_rows = loaded_tags
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
