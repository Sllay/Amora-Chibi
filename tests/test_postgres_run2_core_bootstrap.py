"""Run 2: exercise complete AmoraCore bootstrap with the real PG18 candidate.

All records invented. Production/Aiven/Turso are never selected or accessed.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID
import os

import pytest

from amora.app import AmoraCore
from amora.events import PrivacyScope
from amora.identity import build_initial_registry
from amora.memory.models import MemoryKind, MemoryProvenance, MemoryQuery, MemoryRecord, ProvenanceKind
from amora.memory.postgres_store import PostgresMemoryStore
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Requires local disposable PostgreSQL 18 CI service",
)

def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_full_amora_core_bootstrap_restart_and_health(tmp_path:Path,monkeypatch):
    monkeypatch.setenv("AMORA_HOME",str(tmp_path/"amora-home"))
    monkeypatch.setenv("AMORA_MODE","local")
    monkeypatch.delenv("AMORA_SECRET_LOCAL_API_TOKEN",raising=False)
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=16)
            clock=SimulatedClock(datetime(2026,11,17,tzinfo=timezone.utc))
            first=AmoraCore.bootstrap(clock=clock,storage=backend)
            try:
                report=first.health_report()
                assert report["schema_version"]==18
                assert report["database_integrity"]=="postgres-candidate-unverified"
                assert report["status"]=="degraded"  # never fake certified
                assert report["self_core_identity"]=="ENTITY_AMORA"
                assert len(report["drives"])==7
                assert report["cognitive_usage_ledger"]==[]
                first.cognition.store.record_usage(
                    provider="synthetic-run2",model="invented",
                    task_class="social",success=True,
                    input_tokens=3,output_tokens=4,status_code=200,
                )
                ledger=first.cognition.store.usage_ledger()
                assert len(ledger)==1
                assert ledger[0]["success_count"]==1
                assert ledger[0]["input_tokens"]==3
                assert first.cognition.store.latest_usage(
                    "synthetic-run2",task_class="social",
                )["status_code"]==200
            finally:
                first.close()
            assert backend.opened_connections==0
            second=AmoraCore.bootstrap(clock=clock,storage=backend)
            try:
                again=second.health_report()
                assert again["self_core_identity"]=="ENTITY_AMORA"
                assert again["cognitive_usage_ledger"][0]["output_tokens"]==4
            finally:
                second.close()
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")


def test_old_private_memory_and_new_memory_survive_full_core_restart(tmp_path: Path, monkeypatch):
    """End-to-end Core persistence: older private recall + newly learned memory.

    A local disposable PostgreSQL 18 fixture is the only database touched.
    This does NOT claim independent certification of real Aiven source freshness.
    """
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "continuity-home"))
    monkeypatch.setenv("AMORA_MODE", "local")
    monkeypatch.delenv("AMORA_SECRET_LOCAL_API_TOKEN", raising=False)
    now = datetime(2026, 11, 17, tzinfo=timezone.utc)
    clock = SimulatedClock(now)
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend = synthetic_core_backend(_raw, max_connections=16)

            def record(number: int, text: str) -> MemoryRecord:
                return MemoryRecord(
                    id=UUID(int=number),
                    kind=MemoryKind.EPISODIC,
                    content=text,
                    provenance=MemoryProvenance(
                        kind=ProvenanceKind.OBSERVATION,
                        source="synthetic-memory-continuity",
                        actor_id="PERSON_BK",
                    ),
                    privacy_scope=PrivacyScope.PRIVATE,
                    owner_entity_id="PERSON_BK",
                    remote_processing_allowed=False,
                    created_at=now,
                    updated_at=now,
                    importance=0.9,
                    gate_score=0.9,
                )

            historical = record(
                716401, "jasmimvioleta716 historia antiga ficticia guardada",
            )
            fresh = record(
                716402, "eucaliptolunar716 lembranca nova ficticia persistida",
            )
            old_query = MemoryQuery(
                text="jasmimvioleta716", projection_recipient_id="PERSON_BK",
                require_topic_match=True,
            )
            new_query = MemoryQuery(
                text="eucaliptolunar716", projection_recipient_id="PERSON_BK",
                require_topic_match=True,
            )

            # Seed a preexisting PG memory before the Core starts; the Core must
            # adopt the record without a database reset or privacy widening.
            with closing(PostgresMemoryStore(
                tmp_path / "no-sqlite.sqlite", clock, build_initial_registry(),
                backend=backend,
            )) as seed:
                assert seed.put(historical, insert_only=True) is True

            first = AmoraCore.bootstrap(clock=clock, storage=backend)
            try:
                assert first.memory.store.get(historical.id).content == historical.content
                assert any(hit.memory.id == historical.id
                           for hit in first.memory.store.rank(old_query))
                assert first.memory.store.put(fresh, insert_only=True) is True
                assert any(hit.memory.id == fresh.id
                           for hit in first.memory.store.rank(new_query))
            finally:
                first.close()
            assert backend.opened_connections == 0

            restarted = AmoraCore.bootstrap(clock=clock, storage=backend)
            try:
                assert restarted.memory.store.get(historical.id).content == historical.content
                assert restarted.memory.store.get(fresh.id).content == fresh.content
                assert any(hit.memory.id == historical.id
                           for hit in restarted.memory.store.rank(old_query))
                assert any(hit.memory.id == fresh.id
                           for hit in restarted.memory.store.rank(new_query))
                # The owner-only records never leak to another recipient.
                for token in ("jasmimvioleta716", "eucaliptolunar716"):
                    forbidden = MemoryQuery(
                        text=token,
                        projection_recipient_id="PERSON_UNRELATED",
                        require_topic_match=True,
                    )
                    assert restarted.memory.store.rank(forbidden) == []
                assert restarted.memory.store.count() >= 2
            finally:
                restarted.close()
            assert backend.opened_connections == 0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
