"""Real PostgreSQL/SQLite FTS5 ordering and ACL parity over Unicode corpus."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime,timedelta,timezone
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
    reason="Only isolated real PostgreSQL 18 lab",
)
NOW=datetime(2026,11,17,tzinfo=timezone.utc)
TOPICS=(
    "coelho branco pula na floresta",
    "água cristalina desliza nas pedras",
    "árvore frondosa da montanha",
    "programação do Roblox é complexa",
    "memória persistente do passado",
    "amizade sincera entre duas pessoas",
    "chão úmido cheio de musgo",
    "chapéu vermelho aparece no jardim",
    "história sobre infância inventada",
    "amanhã haverá chuva forte",
    "câmera isométrica olha do alto",
    "régua mede altura de construção",
)
QUERIES=(
    "coelho","água","arvore","programacao","memoria","amizade",
    "chao","chapeu","historia","amanha","camera","regua",
)


def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_pg_fts_rrf_mmr_rank_order_for_72_accented_memories(tmp_path):
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=2)
            clock=SimulatedClock(NOW)
            identities=build_initial_registry()
            path=tmp_path/"sqlite-rank-reference.db"
            with closing(AmoraDatabase(path)) as db:
                db.migrate()
            with closing(MemoryStore(
                path,clock,identities,
            )) as sqlite,closing(PostgresMemoryStore(
                tmp_path/"not-sqlite.db",clock,identities,backend=backend,
            )) as pg:
                for i in range(72):
                    owner="PERSON_BK" if i%7!=0 else "PERSON_OTHER"
                    scope=(PrivacyScope.PRIVATE if i%5==0
                           else PrivacyScope.PUBLIC if i%5==1
                           else PrivacyScope.PERSONAL)
                    item=MemoryRecord(
                        id=UUID(int=50000+i),kind=MemoryKind.SEMANTIC,
                        content=TOPICS[i%12]+f" contexto fictício {i}",
                        summary="Uma descrição imaginária do ambiente",
                        provenance=MemoryProvenance(
                            kind=ProvenanceKind.OBSERVATION,
                            source="synthetic:unicode-stress",
                            actor_id=owner,
                        ),
                        privacy_scope=scope,
                        owner_entity_id=owner,
                        remote_processing_allowed=scope!=PrivacyScope.PRIVATE,
                        created_at=NOW-timedelta(minutes=i),
                        updated_at=NOW-timedelta(minutes=i),
                        importance=0.5+(i%5)*0.075,
                        gate_score=0.9,
                        tags={"ficticio",f"grupo-{i%4}"},
                    )
                    assert sqlite.put(item,insert_only=True) is True
                    assert pg.put(item,insert_only=True) is True
                for term in QUERIES:
                    for remote_only in (False,True):
                        query=MemoryQuery(
                            text=term,projection_recipient_id="PERSON_BK",
                            projection_remote_only=remote_only,limit=10,
                        )
                        old=sqlite.rank(query)
                        new=pg.rank(query)
                        assert [x.memory.id for x in new]==[
                            x.memory.id for x in old
                        ],(term,remote_only)
                        assert all(
                            hit.memory.privacy_scope not in {
                                PrivacyScope.SECRET,PrivacyScope.AUTH,
                            }
                            for hit in new
                        )
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
