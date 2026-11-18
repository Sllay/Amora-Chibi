"""Real PG18 reflection queue idempotency, recovery and edit invalidation."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime,timezone
import os
from types import SimpleNamespace
from uuid import UUID

import pytest

from amora.cognition.models import CognitiveRequest,ConversationMessage,MessageSource
from amora.cognition.postgres_store import PostgresCognitiveStore
from amora.events import PrivacyScope
from amora.messaging.reflection_queue import ReflectionQueue
from amora.storage.database import AmoraDatabase
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",
    reason="Disposable local PostgreSQL 18 only",
)
NOW=datetime(2026,11,17,tzinfo=timezone.utc)

def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_pg_reflection_enqueue_claim_invalidation_and_next_revision(tmp_path):
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=4)
            clock=SimulatedClock(NOW)
            path=tmp_path/"synthetic.db"
            with closing(AmoraDatabase(path,backend=backend)) as db,closing(
                AmoraDatabase(path,backend=backend)
            ) as other_db,closing(PostgresCognitiveStore(
                path,clock,backend=backend,
            )) as cognition_store:
                db.migrate()
                first_turn=UUID(int=92201)
                session="synthetic-discord-dm"
                native_hash="a"*20
                def put_input(turn,revision):
                    meta={
                        "surface":"platform_messaging",
                        "channel_type":"dm",
                        "reflection_enqueue_requested":True,
                        "native_message_id_hash":native_hash,
                        "transport_revision":revision,
                        "memory_consolidation":True,
                        "test_mode":False,
                    }
                    row=ConversationMessage(
                        id=UUID(int=92000+revision),
                        turn_id=turn,
                        speaker_id="PERSON_BK",
                        addressee_id="ENTITY_AMORA",
                        source=MessageSource.TEXT,
                        raw_text="Lembranca sintetica sem dados reais",
                        started_at=NOW,
                        session_id=session,
                        remote_processing_allowed=True,
                        privacy_scope=PrivacyScope.PERSONAL,
                        dedupe_key=f"cognitive-input:{turn}",
                        metadata=meta,
                    )
                    assert cognition_store.put_message(row) is True
                    return CognitiveRequest(
                        turn_id=turn,
                        text=row.raw_text,
                        speaker_id=row.speaker_id,
                        session_id=session,
                        remote_processing_allowed=True,
                        privacy_scope=PrivacyScope.PERSONAL,
                        metadata=meta,
                    )
                request=put_input(first_turn,1)
                first=ReflectionQueue(db,SimpleNamespace(store=cognition_store),clock)
                second=ReflectionQueue(other_db,SimpleNamespace(store=cognition_store),clock)
                assert first.enqueue(request) is True
                assert second.enqueue(request) is False
                assert first.counts()["pending"]==1
                claim=first.claim()
                assert claim is not None and claim[0]==str(first_turn)
                assert second.claim() is None
                assert second.invalidate_source(
                    session,native_hash,through_revision=1,
                )==1
                assert first.source_invalidated(session,native_hash,1)
                assert first.state(first_turn)["status"]=="cancelled"
                assert first.finish(claim,"LATE_SUCCESS") is False
                assert first.enqueue(request) is False
                next_turn=UUID(int=92202)
                next_request=put_input(next_turn,2)
                assert first.reconcile_missing()==1
                assert second.enqueue(next_request) is False
                assert first.counts()["pending"]==1
                second_job=second.claim()
                assert second_job is not None and second_job[0]==str(next_turn)
                assert first.finish(second_job,"REFLECTION_NO_ACCEPTED_MEMORY") is True
                assert first.counts()["done"]==1
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
