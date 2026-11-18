"""Run 2 PostgreSQL memory-only replica fence against real disposable PG18."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime,timezone
from uuid import UUID
import os
import pytest

from amora.events import PrivacyScope
from amora.identity import build_initial_registry
from amora.memory.models import (
    MemoryHit,MemoryKind,MemoryProvenance,MemoryRecord,ProvenanceKind,
)
from amora.memory.postgres_store import PostgresMemoryStore
from amora.storage.postgres_memory_epoch_lab import rotate_pg_memory_epoch_after_restore
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.storage.replica_coherence import ProjectionStamp,ReplicaCoherenceError
from amora.time import SimulatedClock

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",
    reason="Local disposable PG18 and explicit opt-in epoch only",
)
NOW=datetime(2026,11,17,tzinfo=timezone.utc)


def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def _record(number):
    return MemoryRecord(
        id=UUID(int=number),kind=MemoryKind.SEMANTIC,
        content=f"Registro ficticio de memoria {number}",
        provenance=MemoryProvenance(
            kind=ProvenanceKind.OBSERVATION,source="synthetic:replica",
            actor_id="PERSON_BK",
        ),
        privacy_scope=PrivacyScope.PRIVATE,owner_entity_id="PERSON_BK",
        remote_processing_allowed=False,created_at=NOW,updated_at=NOW,
        importance=0.8,gate_score=0.9,
    )


def test_epoch_is_fail_closed_until_explicitly_installed(tmp_path):
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=2)
            with closing(PostgresMemoryStore(
                tmp_path/"unpromoted.db",SimulatedClock(NOW),
                build_initial_registry(),backend=backend,
            )) as store:
                record=_record(3300)
                assert store.put(record,insert_only=True)
                hit=MemoryHit(memory=store.get(record.id),score=0.8)
                with pytest.raises(ReplicaCoherenceError,match="NOT_INSTALLED"):
                    store.record_verified_access(
                        [hit],ProjectionStamp("untrusted",0,"memory"),
                    )
                assert store.get(record.id).access_count==0
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")


def test_pg_epoch_concurrent_mutations_stale_reads_and_restore_fence(tmp_path):
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            status=prepare_disposable_core_v18(
                manager,include_memory_epoch_candidate=True,
            )
            assert status["memory_epoch_candidate_installed"] is True
            backend=synthetic_core_backend(_raw,max_connections=3)
            clock=SimulatedClock(NOW)
            path=tmp_path/"never-sqlite.db"
            with closing(PostgresMemoryStore(
                path,clock,build_initial_registry(),backend=backend,
            )) as first,closing(PostgresMemoryStore(
                path,clock,build_initial_registry(),backend=backend,
            )) as second:
                original=first.read_memory_projection_stamp()
                assert original.scope=="memory"
                assert first.put(_record(3301),insert_only=True)
                current=first.read_memory_projection_stamp()
                assert current.epoch_id==original.epoch_id
                assert current.revision>original.revision
                hit=MemoryHit(memory=first.get(UUID(int=3301)),score=0.8)
                with pytest.raises(ReplicaCoherenceError,match="REVISION_CHANGED"):
                    second.record_verified_access([hit],original)
                assert second.get(UUID(int=3301)).access_count==0
                with pytest.raises(ReplicaCoherenceError,match="SCOPE_UNVERIFIED"):
                    second.record_verified_access(
                        [hit],ProjectionStamp(
                            current.epoch_id,current.revision,"combined",
                        ),
                    )
                second.record_verified_access([hit],current)
                assert first.get(UUID(int=3301)).access_count==1
                # A used stamp cannot be replayed after the access-only write:
                # the conservative epoch invalidates on every record update.
                with pytest.raises(ReplicaCoherenceError,match="REVISION_CHANGED"):
                    first.record_verified_access([hit],current)
                stamp=first.read_memory_projection_stamp()
                phantom=MemoryHit(memory=_record(3399),score=0.8)
                with pytest.raises(ReplicaCoherenceError,match="ROWS_CHANGED"):
                    first.record_verified_access([hit,phantom],stamp)
                assert first.get(UUID(int=3301)).access_count==1
                # Independently edited tags are a projection-relevant mutation.
                manager.execute("""
                    INSERT INTO amora_pg_v18_lab.memory_tags(memory_id,tag)
                    VALUES (%s,%s)
                """,(str(UUID(int=3301)),"synthetic-out-of-band"))
                with pytest.raises(ReplicaCoherenceError,match="REVISION_CHANGED"):
                    first.record_verified_access([hit],stamp)
                before_restore=first.read_memory_projection_stamp()
                new_epoch=rotate_pg_memory_epoch_after_restore(manager)
                assert new_epoch!=before_restore.epoch_id
                with pytest.raises(ReplicaCoherenceError,match="REVISION_CHANGED"):
                    first.record_verified_access([hit],before_restore)
                after=first.read_memory_projection_stamp()
                assert after.epoch_id==new_epoch
                assert after.revision>before_restore.revision
                assert first.record_verified_access([hit],after)==[hit]
                assert second.get(UUID(int=3301)).access_count==2
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
