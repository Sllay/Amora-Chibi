"""Regression for concurrent PG memory decay without lost updates."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime,timezone
import os
from pathlib import Path
from uuid import UUID

import pytest

from amora.identity import build_initial_registry
from amora.events import PrivacyScope
from amora.memory.models import MemoryKind,MemoryRecord,MemoryProvenance,ProvenanceKind
from amora.memory.postgres_store import PostgresMemoryStore
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",
    reason="Disposable local PG18 only",
)
NOW=datetime(2026,11,17,tzinfo=timezone.utc)


def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_concurrent_decay_is_multiplicative_and_atomic(tmp_path:Path):
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=3)
            clock=SimulatedClock(NOW)
            identities=build_initial_registry()
            path=tmp_path/"not-sqlite.db"
            record=MemoryRecord(
                id=UUID(int=55511),kind=MemoryKind.SEMANTIC,
                content="registro ficticio que perde recuperabilidade",
                provenance=MemoryProvenance(
                    kind=ProvenanceKind.OBSERVATION,
                    source="synthetic:decay",actor_id="PERSON_BK",
                ),
                privacy_scope=PrivacyScope.PRIVATE,
                owner_entity_id="PERSON_BK",
                retrievability=1.0,
                created_at=NOW,updated_at=NOW,
                importance=0.8,gate_score=0.9,
            )
            with closing(PostgresMemoryStore(
                path,clock,identities,backend=backend,
            )) as first,closing(PostgresMemoryStore(
                path,clock,identities,backend=backend,
            )) as second:
                assert first.put(record,insert_only=True) is True
                with ThreadPoolExecutor(max_workers=2) as pool:
                    left=pool.submit(first.degrade_retrievability,record.id,0.5)
                    right=pool.submit(second.degrade_retrievability,record.id,0.5)
                    results={round(left.result(),6),round(right.result(),6)}
                assert results=={0.5,0.25}
                assert first.get(record.id).retrievability==pytest.approx(0.25)
                assert first.degrade_retrievability(
                    UUID(int=9876555),0.5,
                ) is None
                assert second.degrade_retrievability(record.id,0.0)==pytest.approx(0.02)
                assert first.get(record.id).retrievability==pytest.approx(0.02)
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
