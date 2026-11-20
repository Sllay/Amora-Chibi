"""Run 2: real transactional sensory episode lease/evidence fence on opt-in PG18.

Invented perception events only. This does not promote the v19 schema.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime,timedelta,timezone
from uuid import UUID
import os

import pytest

from amora.events import PrivacyScope
from amora.events.multimodal import (
    ObservationContext,ObservationSource,SensoryObservation,
    SensoryModality,EvidenceKind,build_observation_event,
)
from amora.identity import build_initial_registry
from amora.memory.episodic_observations import EpisodeSelection,episode_record_id
from amora.memory.episode_fence import EpisodeWriteLease
from amora.memory.models import MemoryKind,MemoryProvenance,MemoryRecord,ProvenanceKind
from amora.memory.postgres_store import PostgresMemoryStore
from amora.storage.database import AmoraDatabase
from amora.storage.postgres_runtime_connection import PostgresRuntimeError
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",
    reason="Only local disposable PostgreSQL 18; v19 candidate opt-in",
)
NOW=datetime(2026,11,17,tzinfo=timezone.utc)


def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_pg_episode_memory_and_queue_ack_are_atomic(tmp_path):
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            status=prepare_disposable_core_v18(
                manager,include_episode_candidate=True,
            )
            assert status["sensory_episode_candidate_installed"] is True
            assert status["schema19_migration_performed"] is False
            backend=synthetic_core_backend(_raw,max_connections=3)
            clock=SimulatedClock(NOW)
            with closing(AmoraDatabase(
                tmp_path/"synthetic-core.db",backend=backend,
            )) as db,closing(PostgresMemoryStore(
                tmp_path/"synthetic-memory.db",clock,
                build_initial_registry(),backend=backend,
            )) as memory:
                assert db.migrate()==0
                correlation=UUID(int=81772)
                context=ObservationContext(
                    source=ObservationSource.WEB,
                    session_id="synthetic-session",
                    scope_id=UUID(int=81775),
                    correlation_id=correlation,
                    stream_id=UUID(int=81776),
                    epoch=0,actor_id="PERSON_BK",
                    owner_entity_id="PERSON_BK",
                    audience=("PERSON_BK",),
                    privacy_scope=PrivacyScope.PRIVATE,
                    remote_processing_allowed=False,
                )
                observation=SensoryObservation(
                    modality=SensoryModality.IMAGE,
                    evidence_kind=EvidenceKind.OBSERVED,
                    sequence=1,
                    capture_started_at=NOW,
                    capture_ended_at=NOW,
                    received_at=NOW,
                    confidence=0.9,
                    text="Observacao sintetica de um jardim",
                )
                event=build_observation_event(context,observation)
                source_id=event.id
                assert db.append_event(event) is True
                selection=EpisodeSelection(
                    evidence_ids=(source_id,),recipient_id="PERSON_BK",
                )
                job_id=episode_record_id(selection.evidence_ids)
                token=UUID(int=81773)
                encoded=selection.model_dump_json()
                manager.execute("""
                    INSERT INTO amora_pg_v18_lab.sensory_episode_jobs(
                        job_id,selection_json,session_id,correlation_id,
                        status,attempts,created_at,available_at,
                        lease_token,lease_until
                    ) VALUES (
                        %s,%s,%s,%s,'claimed',1,%s,%s,%s,%s
                    )
                """,(
                    str(job_id),encoded,"synthetic-session",
                    str(correlation),NOW.isoformat(),NOW.isoformat(),
                    str(token),(NOW+timedelta(minutes=5)).isoformat(),
                ))
                manager.execute("""
                    INSERT INTO amora_pg_v18_lab.sensory_episode_job_evidence
                    (job_id,event_id) VALUES (%s,%s)
                """,(str(job_id),str(source_id)))
                record=MemoryRecord(
                    id=job_id,kind=MemoryKind.EPISODIC,
                    content="Observacao sintetica do jardim, sem midia externa",
                    provenance=MemoryProvenance(
                        kind=ProvenanceKind.DERIVED,
                        source="perception:episode:v1",
                        source_event_id=source_id,
                        session_id="synthetic-session",
                        actor_id="PERSON_BK",
                    ),
                    privacy_scope=PrivacyScope.PRIVATE,
                    owner_entity_id="PERSON_BK",
                    remote_processing_allowed=False,
                    created_at=NOW,updated_at=NOW,
                    importance=0.9,gate_score=0.9,
                    metadata={
                        "evidence_event_ids":[str(source_id)],
                        "correlation_id":str(correlation),
                    },
                )
                lost_lease=EpisodeWriteLease(
                    job_id,UUID(int=81774),1,encoded,
                )
                with pytest.raises(PostgresRuntimeError,match="EPISODE_WRITE_LEASE_LOST"):
                    memory.put(record,insert_only=True,episode_lease=lost_lease)
                assert memory.get(job_id) is None
                assert memory.put(
                    record,insert_only=True,
                    episode_lease=EpisodeWriteLease(job_id,token,1,encoded),
                ) is True
                assert manager.execute("""
                    SELECT status,lease_token FROM amora_pg_v18_lab.sensory_episode_jobs
                    WHERE job_id=%s
                """,(str(job_id),)).fetchone()==("done",None)
                assert memory.get(job_id).content==record.content
                with pytest.raises(PostgresRuntimeError,match="EPISODE_WRITE_LEASE_LOST"):
                    memory.put(
                        record,insert_only=True,
                        episode_lease=EpisodeWriteLease(job_id,token,1,encoded),
                    )
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
