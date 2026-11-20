"""Run 2: execute real sensory episode repository on disposable PG18."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime,timedelta,timezone
from pathlib import Path
import os
from uuid import UUID

import pytest

from amora.events import PrivacyScope
from amora.events.multimodal import (
    EvidenceKind,ObservationContext,ObservationSource,
    SensoryModality,SensoryObservation,build_observation_event,
)
from amora.identity import build_initial_registry
from amora.memory.episode_work import EnqueueResult,EpisodeWorkRepository
from amora.memory.episodic_observations import EpisodeSelection,EpisodicObservationConsolidator
from amora.memory.postgres_store import PostgresMemoryStore
from amora.memory.service import MemoryService
from amora.storage.database import AmoraDatabase
from amora.storage.postgres_runtime_connection import PostgresRuntimeError
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",
    reason="Isolated local PG18 sensory candidate only",
)
NOW=datetime(2026,11,17,tzinfo=timezone.utc)

def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_pg_episode_restart_stale_lease_process_invalidation(tmp_path:Path):
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager,include_episode_candidate=True)
            backend=synthetic_core_backend(_raw,max_connections=4)
            clock=SimulatedClock(NOW)
            identities=build_initial_registry()
            path=tmp_path/"episode-synthetic-only.db"
            with closing(AmoraDatabase(path,backend=backend)) as db,closing(
                AmoraDatabase(path,backend=backend)
            ) as restarted_db,closing(PostgresMemoryStore(
                path,clock,identities,backend=backend,
            )) as store:
                db.migrate()
                memory=MemoryService(store,clock)
                event=build_observation_event(
                    ObservationContext(
                        source=ObservationSource.WEB,
                        session_id="synthetic-perception-session",
                        scope_id=UUID(int=4567),stream_id=UUID(int=4568),
                        correlation_id=UUID(int=4569),epoch=1,
                        actor_id="PERSON_BK",owner_entity_id="PERSON_BK",
                        audience=("PERSON_BK",),
                        privacy_scope=PrivacyScope.PRIVATE,
                        remote_processing_allowed=False,
                    ),
                    SensoryObservation(
                        modality=SensoryModality.IMAGE,
                        evidence_kind=EvidenceKind.OBSERVED,
                        sequence=1,
                        capture_started_at=NOW,capture_ended_at=NOW,
                        received_at=NOW,confidence=0.95,
                        text="Cenoura amarela em um jardim inventado",
                    ),
                )
                assert db.append_event(event) is True
                selection=EpisodeSelection(
                    evidence_ids=(event.id,),recipient_id="PERSON_BK",
                    importance=.9,novelty=.9,goal_relevance=.9,
                )
                work=EpisodeWorkRepository(
                    EpisodicObservationConsolidator(db,memory),
                    max_attempts=2,lease_seconds=5,
                )
                recovery=EpisodeWorkRepository(
                    EpisodicObservationConsolidator(restarted_db,memory),
                    max_attempts=2,lease_seconds=5,
                )
                assert work.enqueue(selection)==EnqueueResult.ACCEPTED
                assert recovery.enqueue(selection)==EnqueueResult.DUPLICATE
                first=work.claim()
                assert first is not None and first.attempts==1
                assert recovery.claim() is None
                clock.advance(timedelta(seconds=6))
                second=recovery.claim()
                assert second is not None and second.attempts==2
                assert second.lease_token!=first.lease_token
                with pytest.raises(PostgresRuntimeError,match="LEASE_LOST"):
                    work.process(first)
                assert store.count()==0
                outcome=recovery.process(second)
                assert outcome.memory is not None
                assert outcome.memory.privacy_scope==PrivacyScope.PRIVATE
                assert outcome.memory.remote_processing_allowed is False
                assert store.count()==1
                assert work.state(second.job_id)["status"]=="done"
                assert work.claim() is None
                # Invalidate source after completion. The archived memory must
                # no longer appear in scoped retrieval and must not reenqueue.
                assert work.invalidate_evidence(event.id)==1
                assert store.get(second.job_id).archived_at is not None
                assert store.count()==0
                with pytest.raises(ValueError,match="EPISODE_EVIDENCE_INVALIDATED"):
                    recovery.enqueue(selection)
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
