"""Real PostgreSQL cognition V1 metadata and spoken-prefix regression gates."""
from __future__ import annotations

from contextlib import closing
from datetime import datetime,timezone
import os
from pathlib import Path
from uuid import UUID

import pytest

from amora.cognition.models import (
    ConversationMessage,MessageSource,MessageStatus,SpeechSegment,
)
from amora.cognition.postgres_store import PostgresCognitiveStore
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",
    reason="Local disposable PostgreSQL 18 only",
)
STAMP=datetime(2026,11,17,tzinfo=timezone.utc)


def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_pg_cognition_metadata_merge_interrupted_speech_and_restart(tmp_path:Path):
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=2)
            path=tmp_path/"not-sqlite.db"
            clock=SimulatedClock(STAMP)
            with closing(PostgresCognitiveStore(
                path,clock,backend=backend,
            )) as store:
                message=ConversationMessage(
                    id=UUID(int=8181),turn_id=UUID(int=8182),
                    speaker_id="PERSON_BK",addressee_id="ENTITY_AMORA",
                    source=MessageSource.TEXT,
                    raw_text="msg de teste",started_at=STAMP,
                    metadata={"first":1},dedupe_key="run2:v1:message",
                )
                assert store.put_message(message) is True
                assert store.merge_message_metadata(
                    message.id,{"second":{"value":2}},
                ) is True
                assert store.merge_message_metadata(
                    message.id,{"third":3},
                ) is True
                assert store.merge_message_metadata(
                    UUID(int=12345),{"unknown":True},
                ) is False
                observed=store.get_message_by_dedupe("run2:v1:message")
                assert observed.metadata=={
                    "first":1,"second":{"value":2},"third":3,
                }
                segment=SpeechSegment(
                    id=UUID(int=8183),turn_id=message.turn_id,
                    ordinal=0,display_text="olha Amora",
                    speech_text="olha Amora",queued_at=STAMP,
                    metadata={"message_id":str(message.id)},
                )
                assert store.put_segment(segment) is True
                assert store.record_interrupted_progress(
                    segment.id,spoken_chars=4,
                )=="olha"
                assert store.get_message_by_dedupe(
                    "run2:v1:message",
                ).status==MessageStatus.INTERRUPTED
            with closing(PostgresCognitiveStore(
                path,clock,backend=backend,
            )) as restarted:
                assert restarted.get_message_by_dedupe(
                    "run2:v1:message",
                ).metadata["third"]==3
                assert restarted.list_segments(message.turn_id)[0].metadata[
                    "spoken_chars"
                ]==4
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
