"""Injected PostgreSQL candidate WorldStore with bound dynamic query builders."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path

from amora.storage.backend import StorageBackend
from amora.storage.postgres_runtime_connection import PostgresRuntimeError
from amora.storage.postgres_runtime_world import (
    CLAIM_QUERY,SELF_LIST,REL_HISTORY,EVIDENCE_BULK,
)
from amora.time import Clock

from .store import WorldStore
from .models import WorldQuery,SelfLayer


class PostgresWorldStore(WorldStore):
    def __init__(self,path:Path,clock:Clock,*,backend:StorageBackend):
        if backend.capabilities.name!="postgres-candidate":
            raise PostgresRuntimeError("PG_WORLD_BACKEND_REQUIRED")
        super().__init__(path,clock,backend=backend)

    def query_claims(self,query:WorldQuery,*,at:datetime|None=None):
        kinds=sorted(kind.value for kind in query.kinds) if query.kinds else None
        instant=(at or self.clock.now()).isoformat()
        args=(
            query.subject_id,query.subject_id,
            query.predicate,query.predicate,
            query.context_key,query.context_key,
            kinds,kinds,
            bool(query.canonical_only),bool(query.include_expired),
            instant,instant,max(1,min(500,int(query.limit))),
        )
        with self._lock:
            rows=self._conn.execute(CLAIM_QUERY,args).fetchall()
        return self._hydrate_claims(rows)

    def list_self_entries(self,layer:SelfLayer|None=None):
        key=layer.value if layer is not None else None
        with self._lock:
            rows=self._conn.execute(SELF_LIST,(key,key)).fetchall()
        return [self._hydrate_self(row) for row in rows]

    def relationship_history(self,source_id:str,*,target_id:str|None=None):
        with self._lock:
            rows=self._conn.execute(
                REL_HISTORY,(source_id,target_id,target_id),
            ).fetchall()
        return [self._hydrate_relationship(row) for row in rows]

    def _hydrate_claims(self,rows):
        if not rows:
            return []
        ids=[str(row["id"]) for row in rows]
        with self._lock:
            evidence=self._conn.execute(EVIDENCE_BULK,(ids,)).fetchall()
        by_claim=defaultdict(list)
        for row in evidence:
            by_claim[str(row["claim_id"])].append(row)
        return [self._hydrate_claim(
            row,evidence_rows=by_claim.get(str(row["id"]),()),
        ) for row in rows]
