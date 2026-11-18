"""Run 2: compare canonical SQLite FTS5 RRF/MMR against PG real store.

Invented records only, with per-owner privacy filters and Unicode accents.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime,timedelta,timezone
from pathlib import Path
from uuid import UUID
import os

import pytest

from amora.events import PrivacyScope
from amora.identity import build_initial_registry
from amora.memory.models import (
    MemoryKind,MemoryRecord,MemoryProvenance,MemoryQuery,ProvenanceKind,
)
from amora.memory.store import MemoryStore
from amora.memory.postgres_store import PostgresMemoryStore
from amora.storage.database import AmoraDatabase
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",
    reason="Disposable PostgreSQL 18 only",
)
NOW=datetime(2026,11,17,tzinfo=timezone.utc)

def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_sqlite_pg_bm25_rrf_mmr_privacy_parity(tmp_path:Path):
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=2)
            clock=SimulatedClock(NOW)
            identities=build_initial_registry()
            path=tmp_path/"sqlite-reference.db"
            with closing(AmoraDatabase(path)) as schema:
                schema.migrate()
            records=[
                MemoryRecord(
                    id=UUID(int=i+1),
                    kind=MemoryKind.SEMANTIC,
                    content=content,
                    provenance=MemoryProvenance(
                        kind=ProvenanceKind.OBSERVATION,
                        source="synthetic:parity",actor_id=owner,
                    ),
                    privacy_scope=scope,owner_entity_id=owner,
                    remote_processing_allowed=remote,
                    tags={"memoria","ficcao"},
                    created_at=NOW-timedelta(hours=i),
                    updated_at=NOW-timedelta(hours=i),
                    importance=0.9-(i/10),
                    gate_score=0.9,
                )
                for i,(content,owner,scope,remote) in enumerate([
                    ("Coelho branco salta na floresta","PERSON_BK",
                     PrivacyScope.PERSONAL,True),
                    ("Coelho marrom lembra de uma árvore antiga","PERSON_BK",
                     PrivacyScope.PRIVATE,False),
                    ("Floresta úmida e pinheiros verdes","PERSON_BK",
                     PrivacyScope.PUBLIC,True),
                    ("Coelho esconde a palavra confidencial","PERSON_OTHER",
                     PrivacyScope.PRIVATE,False),
                    ("Coelho de testes não deve circular","PERSON_BK",
                     PrivacyScope.AUTH,False),
                ])
            ]
            with closing(MemoryStore(path,clock,identities)) as sqlite,closing(
                PostgresMemoryStore(
                    tmp_path/"not-sqlite.db",clock,identities,backend=backend,
                )
            ) as pg:
                for record in records:
                    sqlite.put(record,insert_only=True)
                    pg.put(record,insert_only=True)
                for text,remote_only in [
                    ("coelho",False),
                    ("arvore antiga",False),
                    ("floresta",True),
                ]:
                    query=MemoryQuery(
                        text=text,projection_recipient_id="PERSON_BK",
                        projection_remote_only=remote_only,limit=4,
                    )
                    old=sqlite.rank(query)
                    migrated=pg.rank(query)
                    assert [h.memory.id for h in migrated]==[
                        h.memory.id for h in old
                    ],(text,[h.memory.id for h in old],[h.memory.id for h in migrated])
                    assert all(
                        h.memory.owner_entity_id=="PERSON_BK"
                        or h.memory.privacy_scope==PrivacyScope.PUBLIC
                        for h in migrated
                    )
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
