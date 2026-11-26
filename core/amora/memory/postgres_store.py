"""PostgreSQL candidate MemoryStore using canonical ranking and hydration.

The same in-memory RRF/MMR scoring and MemoryRecord models are reused from
MemoryStore, while reviewed PostgreSQL statements handle candidate filtering
and transactional text/term-posting maintenance.

The unported lease/replica/owner-turn flows fail closed, not via SQLite
fallback. This class is not selected for production by environment.
"""
from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Iterable
from uuid import UUID

from amora.storage.backend import SQLiteBackend, StorageBackend
from amora.storage.postgres_runtime_connection import PostgresRuntimeError
from amora.storage import postgres_runtime_memory as q
from amora.storage.postgres_atomic_index_lab import _document_terms
from amora.storage.postgres_fts_bm25_lab import normalized_fts5_terms
from amora.time import Clock
from amora.identity import EntityRegistry

from .episode_fence import EpisodeWriteLease
from .models import MemoryRecord, MemoryQuery, MemoryHit
from .store import MemoryStore, _tokens, _retrieval_tokens


class PostgresMemoryStore(MemoryStore):
    """Inject only with PostgresBackendCandidate + complete reviewed registry."""

    def __init__(
        self, path: Path, clock: Clock, identities: EntityRegistry,
        *, backend: StorageBackend,
    ) -> None:
        if backend.capabilities.name != "postgres-candidate":
            raise PostgresRuntimeError("PG_MEMORY_BACKEND_REQUIRED")
        super().__init__(path, clock, identities, backend=backend)

    def _ensure_fts(self) -> bool:
        row = self._conn.execute(q.READY).fetchone()
        if row is None or row[0] is not True:
            raise PostgresRuntimeError("PG_MEMORY_POSTINGS_NOT_READY")
        return True

    def count(self, *, include_archived: bool = False) -> int:
        with self._lock:
            row = self._conn.execute(q.COUNT, (include_archived,)).fetchone()
        return int(row[0]) if row else 0

    def put(
        self, record: MemoryRecord, *,
        reflection_lease: tuple[str, str] | None = None,
        generation_lease: tuple[str, str] | None = None,
        insert_only: bool = False,
        episode_lease: EpisodeWriteLease | None = None,
    ) -> bool | None:
        if episode_lease is not None and (
                not insert_only or reflection_lease is not None
                or generation_lease is not None):
            raise PostgresRuntimeError("PG_MEMORY_EPISODE_REQUIRES_CREATE_ONLY")
        if insert_only and (reflection_lease is not None or generation_lease is not None):
            raise PostgresRuntimeError("PG_MEMORY_INSERT_ONLY_LEASE_UNSUPPORTED")
        if record.provenance.source == "perception:episode:v1" and episode_lease is None:
            raise PostgresRuntimeError("PG_MEMORY_EPISODE_WRITE_REQUIRES_LEASE")
        from .versioned_fact import checked_version_key, link_version, VersionedFactConflict
        version_key = checked_version_key(record, generation_lease)
        p = record.provenance
        ident = str(record.id)
        values = (
            ident,record.kind.value,record.content,record.summary,p.kind.value,
            p.source,p.confidence,
            str(p.source_event_id) if p.source_event_id else None,
            p.actor_id,p.session_id,
            json.dumps([str(value) for value in p.derived_from],separators=(",",":")),
            record.privacy_scope.value,record.owner_entity_id,
            json.dumps(sorted(record.audience),ensure_ascii=False,separators=(",",":")),
            1 if record.remote_processing_allowed else 0,
            record.valid_from.isoformat() if record.valid_from else None,
            record.valid_until.isoformat() if record.valid_until else None,
            record.created_at.isoformat(),record.updated_at.isoformat(),
            record.importance,record.gate_score,record.retrievability,
            record.access_count,
            record.last_accessed_at.isoformat() if record.last_accessed_at else None,
            record.archived_at.isoformat() if record.archived_at else None,
            json.dumps(record.metadata,ensure_ascii=False,separators=(",",":")),
        )
        content = " ".join(filter(None,(record.content,record.summary or "")))
        tags = " ".join(sorted(record.tags))
        document_length,posting = _document_terms(content,tags)
        with self._lock, self._conn:
            if episode_lease is not None:
                from .postgres_episode_fence import check_pg_episode_write
                check_pg_episode_write(
                    self._conn,record,episode_lease,self.clock.now(),
                )
            if version_key is not None:
                # Source invalidation writes the tombstone and archives source
                # memories in one transaction. Do not race between its check
                # and the new version's commit.
                self._conn.execute(q.VERSION_SOURCE_LOCK)
            if generation_lease is not None:
                turn,token = generation_lease
                lease = self._conn.execute(q.GENERATION_LEASE,(
                    turn,token,self.clock.now().isoformat(),
                    record.owner_entity_id,p.session_id,record.privacy_scope.value,
                    int(record.remote_processing_allowed),
                )).fetchone()
                if lease is None:
                    raise PostgresRuntimeError("INBOUND_GENERATION_LEASE_LOST")
            if reflection_lease is not None:
                turn,token = reflection_lease
                lease = self._conn.execute(q.REFLECTION_LEASE,(
                    turn,token,self.clock.now().isoformat(),
                )).fetchone()
                if lease is None:
                    raise PostgresRuntimeError("REFLECTION_LEASE_LOST")
            if version_key is not None:
                self._conn.execute(q.VERSION_LOCK,(record.owner_entity_id+":"+version_key,))
                predecessor = self._conn.execute(q.VERSION_PREDECESSOR,(record.owner_entity_id,version_key)).fetchone()
                link_version(record,predecessor)
                values = (*values[:-1],json.dumps(record.metadata,ensure_ascii=False,separators=(",",":")))
            result = self._conn.execute(q.CREATE if insert_only or version_key is not None else q.PUT,values)
            if version_key is not None:
                if result.rowcount != 1:
                    raise VersionedFactConflict("VERSIONED_FACT_ID_CONFLICT")
                now=self.clock.now().isoformat()
                self._conn.execute(q.VERSION_ARCHIVE,(now,now,record.owner_entity_id,version_key,ident))
            if insert_only and result.rowcount == 0:
                if episode_lease is not None:
                    from .postgres_episode_fence import complete_pg_episode_write
                    complete_pg_episode_write(
                        self._conn,record,episode_lease,
                        self.clock.now(),inserted=False,
                    )
                return False
            if result.rowcount != 1:
                raise PostgresRuntimeError("PG_MEMORY_WRITE_OUTCOME_UNKNOWN")
            self._conn.execute(q.DEL_ENTITIES,(ident,))
            if record.entity_refs:
                self._conn.executemany(q.ADD_ENTITIES,(
                    (ident,key,confidence)
                    for key,confidence in sorted(record.entity_refs.items())
                ))
            self._conn.execute(q.DEL_TAGS,(ident,))
            if record.tags:
                self._conn.executemany(q.ADD_TAGS,(
                    (ident,tag) for tag in sorted(record.tags)
                ))
            self._conn.execute(q.UPSERT_SEARCH,(ident,content,tags))
            self._conn.execute(q.UPSERT_DOC,(ident,document_length))
            self._conn.execute(q.DEL_POSTINGS,(ident,))
            if posting:
                self._conn.executemany(q.ADD_POSTINGS,(
                    (ident,term,count) for term,count in posting
                ))
            actual = self._conn.execute(q.COUNT_POSTINGS,(ident,)).fetchone()
            if actual is None or int(actual[0]) != document_length:
                raise PostgresRuntimeError("PG_MEMORY_ATOMIC_INDEX_DRIFT")
            if episode_lease is not None:
                from .postgres_episode_fence import complete_pg_episode_write
                complete_pg_episode_write(
                    self._conn,record,episode_lease,
                    self.clock.now(),inserted=True,
                )
        return True if insert_only else None

    def get(self, memory_id: UUID | str) -> MemoryRecord | None:
        # This internal API matches MemoryStore.get(): the caller authorizes
        # raw retrieval. Public-facing reads must use the scoped rank path.
        with self._lock:
            row = self._conn.execute(q.GET,(str(memory_id),)).fetchone()
            if row is None:
                return None
            return next(iter(self._hydrate_many([row]).values()),None)

    @staticmethod
    def _scope_args(query: MemoryQuery, now: str) -> tuple:
        recipient = query.projection_recipient_id
        kinds = sorted(kind.value for kind in query.kinds) if query.kinds else None
        after = query.after.isoformat() if query.after else None
        before = query.before.isoformat() if query.before else None
        return (
            int(query.include_archived),now,kinds,kinds,
            after,after,before,before,
            int(query.projection_public_only),int(query.projection_remote_only),
            recipient,recipient,recipient,recipient,
        )

    def _candidate_rows(self, query: MemoryQuery) -> list:
        now = self.clock.now().isoformat()
        args = self._scope_args(query,now)
        with self._lock:
            baseline = self._conn.execute(q.BASELINE,args).fetchall()
            if not query.text.strip():
                return baseline
            terms = normalized_fts5_terms(query.text, runtime_projection=True)
            if not terms:
                return baseline
            found = self._conn.execute(q.FTS_RANK,(terms,*args,None,None)).fetchall()
            matched_ids = [row["id"] for row in found]
            if matched_ids:
                matches = self._conn.execute(q.GET_BULK,(matched_ids,)).fetchall()
            else:
                matches = []
        return list({row["id"]:row for row in [*baseline,*matches]}.values())

    def _text_rank(self, text: str, records: dict[str,MemoryRecord]) -> list[str]:
        terms = normalized_fts5_terms(text, runtime_projection=True)
        if not terms or not records:
            return []
        now = self.clock.now().isoformat()
        # Existing rank() applied all privacy, recipient and topic filters
        # to records before calling _text_rank. Restrict by that exact set.
        no_scope = (
            1,now,None,None,None,None,None,None,
            0,0,None,None,None,None,
        )
        with self._lock:
            rows = self._conn.execute(
                q.FTS_RANK,(terms,*no_scope,list(records),list(records)),
            ).fetchall()
        indexed = [row["id"] for row in rows if row["id"] in records]
        if indexed:
            return indexed
        # Preserve canonical MemoryStore behavior when an indexed search has
        # no hits. A lack of BM25 postings must not silently suppress valid
        # direct lexical overlap among already authorized candidates.
        query_set = set(_retrieval_tokens(text))
        scored: list[tuple[float, str]] = []
        for memory_id, record in records.items():
            haystack = _tokens(" ".join((
                record.content,record.summary or ""," ".join(record.tags),
            )))
            if not haystack:
                continue
            overlap = len(query_set & haystack)
            if overlap:
                scored.append((overlap / len(query_set | haystack),memory_id))
        scored.sort(reverse=True)
        return [memory_id for _, memory_id in scored]

    def _entity_ranks(self,entity_ids:set[str],candidate_ids:Iterable[str]):
        if not entity_ids:
            return [],[]
        candidates=set(candidate_ids)
        with self._lock:
            exact_rows=self._conn.execute(
                q.ENTITY_RANK,(sorted(entity_ids),),
            ).fetchall()
        exact=[
            row["memory_id"] for row in exact_rows
            if row["memory_id"] in candidates
        ]
        related_entities=set()
        for relation in self.identities.relationships:
            if relation.source in entity_ids:
                related_entities.add(relation.target)
            if relation.target in entity_ids:
                related_entities.add(relation.source)
        related_entities-=entity_ids
        related=[]
        if related_entities:
            with self._lock:
                related_rows=self._conn.execute(
                    q.ENTITY_RANK,(sorted(related_entities),),
                ).fetchall()
            seen=set(exact)
            related=[
                row["memory_id"] for row in related_rows
                if row["memory_id"] in candidates and row["memory_id"] not in seen
            ]
        return list(dict.fromkeys(exact)),list(dict.fromkeys(related))

    def local_only_reflection_audit(self)->dict:
        with self._lock:
            total=self._conn.execute(
                q.REFLECTION_AUDIT_COUNT,
            ).fetchone()
            rows=self._conn.execute(
                q.REFLECTION_AUDIT_ROWS,
            ).fetchall()
        return {
            "total":int(total[0]) if total else 0,
            "review_candidates":[dict(row) for row in rows],
        }

    def _hydrate_many(self, rows: list) -> dict[str,MemoryRecord]:
        if not rows:
            return {}
        ids = [str(row["id"]) for row in rows]
        if len(ids)>450:
            raise PostgresRuntimeError("PG_MEMORY_HYDRATION_LIMIT")
        with self._lock:
            entities = self._conn.execute(q.ENTITIES_BULK,(ids,)).fetchall()
            tags = self._conn.execute(q.TAGS_BULK,(ids,)).fetchall()
        by_entity: dict[str,list] = defaultdict(list)
        by_tag: dict[str,list] = defaultdict(list)
        for row in entities:
            by_entity[str(row["memory_id"])].append(row)
        for row in tags:
            by_tag[str(row["memory_id"])].append(row)
        return {
            str(row["id"]):self._hydrate(
                row,entity_rows=by_entity.get(str(row["id"]),()),
                tag_rows=by_tag.get(str(row["id"]),()),
            )
            for row in rows
        }

    def archive(self, memory_id: UUID | str) -> bool:
        stamp = self.clock.now().isoformat()
        with self._lock, self._conn:
            return self._conn.execute(
                q.ARCHIVE,(stamp,stamp,str(memory_id))
            ).rowcount == 1

    def degrade_retrievability(self, memory_id: UUID | str, factor: float) -> float | None:
        factor = max(0.0,min(1.0,float(factor)))
        # One server-side row update prevents two concurrent stores from
        # overwriting each other's decay after both read the old value.
        with self._lock, self._conn:
            row=self._conn.execute(q.DEGRADE,(
                factor,self.clock.now().isoformat(),str(memory_id),
            )).fetchone()
            return float(row[0]) if row is not None else None

    def link(self, source_memory_id: UUID | str, target_memory_id: UUID | str,
             link_type: str, confidence: float=1.0) -> None:
        confidence = max(0.0,min(1.0,float(confidence)))
        with self._lock, self._conn:
            self._conn.execute(q.LINK,(
                str(source_memory_id),str(target_memory_id),link_type,confidence,
            ))

    def _mark_accessed(self, memory_ids: list[str], now: datetime) -> int:
        if not memory_ids:
            return 0
        with self._lock, self._conn:
            return self._conn.execute(q.ACCESS,(
                now.isoformat(),list(memory_ids),
            )).rowcount

    def read_memory_projection_stamp(self):
        """Read the authoritative opt-in PostgreSQL revision, not a replica."""
        from amora.storage.replica_coherence import (
            ProjectionStamp, ReplicaCoherenceError,
        )
        with self._lock:
            ready=self._conn.execute(q.EPOCH_READY).fetchone()
            if ready is None or ready[0] is not True:
                raise ReplicaCoherenceError("PG_MEMORY_EPOCH_NOT_INSTALLED")
            row=self._conn.execute(q.EPOCH_STAMP).fetchone()
        if row is None:
            raise ReplicaCoherenceError("PG_MEMORY_EPOCH_NOT_INSTALLED")
        return ProjectionStamp(row["epoch_id"],int(row["revision"]),"memory")

    def record_verified_access(self, hits: list[MemoryHit], stamp) -> list[MemoryHit]:
        """Validate a PG-authoritative memory revision and write access once.

        The same transaction locks the epoch row and updates ALL requested
        records. A mismatch rolls back completely. Ambiguous commit failures
        propagate without retry. Never accept a combined/SQLite stamp.
        """
        from amora.storage.replica_coherence import (
            ProjectionStamp, ReplicaCoherenceError,
        )
        if not hits:
            return hits
        if not isinstance(stamp,ProjectionStamp) or stamp.scope!="memory":
            raise ReplicaCoherenceError("PG_MEMORY_EPOCH_SCOPE_UNVERIFIED")
        ids=[str(hit.memory.id) for hit in hits]
        if len(ids)>50 or len(ids)!=len(set(ids)):
            raise ReplicaCoherenceError("REPLICA_DUPLICATE_MEMORY_HIT")
        now=self.clock.now()
        iso=now.isoformat()
        with self._lock,self._conn:
            ready=self._conn.execute(q.EPOCH_READY).fetchone()
            if ready is None or ready[0] is not True:
                raise ReplicaCoherenceError("PG_MEMORY_EPOCH_NOT_INSTALLED")
            epoch=self._conn.execute(q.EPOCH_LOCK).fetchone()
            if (epoch is None or epoch["epoch_id"]!=stamp.epoch_id
                    or int(epoch["revision"])!=stamp.revision):
                raise ReplicaCoherenceError("REPLICA_ACCESS_REVISION_CHANGED")
            updated=self._conn.execute(q.VERIFIED_ACCESS,(iso,ids)).fetchall()
            counts={str(row["id"]):row["access_count"] for row in updated}
            if (len(updated)!=len(ids) or set(counts)!=set(ids)
                    or any(type(value) is not int or value<1 for value in counts.values())
                    or any(row["last_accessed_at"]!=iso for row in updated)):
                raise ReplicaCoherenceError("REPLICA_ACCESS_ROWS_CHANGED")
        for hit in hits:
            hit.memory.access_count=counts[str(hit.memory.id)]
            hit.memory.last_accessed_at=now
        return hits

    def authorize_reviewed_reflection(self, memory_id: UUID | str, owner_id: str) -> bool:
        if self.identities.get(owner_id) is None:
            return False
        with self._lock, self._conn:
            return self._conn.execute(q.AUTHORIZE_REFLECTION,(
                owner_id,self.clock.now().isoformat(),str(memory_id),
                owner_id,owner_id,owner_id,
            )).rowcount == 1

    def owner_turn_memory_status(self, turn_id: str, owner_id: str) -> list[dict]:
        with self._lock:
            rows = self._conn.execute(q.OWNER_TURN_STATUS,(
                owner_id,str(turn_id),
            )).fetchall()
        now = self.clock.now().isoformat()
        return [{
            "memory_id":str(row["id"]),"privacy_scope":row["privacy_scope"],
            "remote_processing_allowed":bool(row["remote_processing_allowed"]),
            "active":row["archived_at"] is None and
                (row["valid_from"] is None or row["valid_from"] <= now) and
                (row["valid_until"] is None or row["valid_until"] >= now),
            "archived":row["archived_at"] is not None,
            "provenance_kind":row["provenance_kind"],
            "provenance_source":row["provenance_source"],
        } for row in rows]

    def shareable_owner_record(self, owner_id: str, record_id: str) -> MemoryRecord | None:
        stamp = self.clock.now().isoformat()
        with self._lock:
            row = self._conn.execute(q.SHAREABLE_OWNER,(
                owner_id,stamp,stamp,str(record_id),
            )).fetchone()
        if row is None:
            return None
        return next(iter(self._hydrate_many([row]).values()),None)

    def recall_owner_records(
        self, owner_id: str, topic: str, *, generic: bool = False,
        limit: int = 3, public_only: bool = False, share_only: bool = False,
    ) -> tuple[list[MemoryRecord],bool]:
        from .recall_intent import topic_terms
        terms = topic_terms(topic)
        if not owner_id or (not generic and not terms):
            return [], True
        limit = max(1,min(5,int(limit)))
        stamp = self.clock.now().isoformat()
        selected: list = []
        seen: set[str] = set()
        complete = False

        def collect(row) -> None:
            if str(row["id"]) in seen:
                return
            if not generic and not terms <= topic_terms(
                str(row["content"])+" "+str(row["summary"] or ""),
            ):
                return
            if share_only:
                # Full canonical disclosure eligibility is checked *before*
                # consuming the result cap, including source invalidations.
                row = self._conn.execute(q.SHAREABLE_OWNER,(
                    owner_id,stamp,stamp,str(row["id"]),
                )).fetchone()
                if row is None:
                    return
            seen.add(str(row["id"]))
            selected.append(row)

        with self._lock:
            if not generic:
                # FTS postings search all eligible owner records before the
                # cap: a memory from years ago must not be hidden by 512 new
                # unrelated memories. Exact topic semantics remain in Python.
                indexed_terms = normalized_fts5_terms(" ".join(sorted(terms)), runtime_projection=True)
                if indexed_terms:
                    # FTS indexes tags as well as content. A result can match
                    # only its tags while failing canonical topic_terms(text).
                    # Page the owner-scoped index before fallback. A hard
                    # 131k-row safety cap prevents unbounded remote reads;
                    # incomplete scans always report complete=False.
                    for index_page in range(256):
                        indexed=self._conn.execute(q.OWNER_RECALL_FTS,(
                            indexed_terms,owner_id,stamp,stamp,
                            int(public_only),index_page*512,
                        )).fetchall()
                        for row in indexed:
                            collect(row)
                            if len(selected) >= limit:
                                indexed_exhausted=len(indexed)<512
                                unindexed=self._conn.execute(
                                    q.OWNER_RECALL_UNINDEXED,(
                                        owner_id,stamp,stamp,int(public_only),
                                    ),
                                ).fetchone()
                                complete=(
                                    indexed_exhausted and unindexed is not None
                                    and unindexed[0] is False
                                )
                                return list(self._hydrate_many(selected[:limit]).values()),complete
                        if len(indexed)<512:
                            break
            # Fallback for legacy documents without postings and for cases
            # where FTS tokens differ from the canonical topic_terms parser.
            # Scan a finite, generous 131k-row candidate window. Return
            # complete=False if the corpus is larger or the limit stops us;
            # never claim an unverified exhaustive search.
            for page in range(256):
                rows = self._conn.execute(q.OWNER_RECALL,(
                    owner_id,stamp,stamp,int(public_only),512,page*512,
                )).fetchall()
                for row in rows:
                    collect(row)
                    if len(selected) >= limit:
                        break
                if len(rows) < 512:
                    complete = True
                    break
                if len(selected) >= limit:
                    break
        return list(self._hydrate_many(selected[:limit]).values()),complete
