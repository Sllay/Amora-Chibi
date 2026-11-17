"""PG18 voice-stop tombstone atomicity and adversarial interleavings.

A deliberately stricter contract than SQLite v18: no canceled turn can
resurrect even if the stop arrives before the first segment. Synthetic only.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from threading import Barrier
from types import SimpleNamespace
from uuid import UUID

import os
import pytest

from amora.conversation.models import SpeechUtterance, UtteranceStatus
from amora.storage import postgres_voice_stop_lab as stop
from amora.storage import postgres_speech_leases_lab as voice
from amora.storage import postgres_v18_lab_schema as schema
from amora.storage.postgres_memory_lab import ensure_local_lab

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Disposable PostgreSQL 18 loopback CI only",
)
NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def connect():
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )


@pytest.fixture
def pg():
    conn = connect()
    try:
        ensure_local_lab(conn)
        conn.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        schema.install_pg_v18_lab(conn)
        stop.install_stop_table(conn)
        yield conn
    finally:
        conn.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        conn.close()


def sample(number, turn="turn-a"):
    return SpeechUtterance(
        id=UUID(int=number), text="frase inventada",
        device_id="PHONE", created_at=NOW,
        dedupe_key=f"speech:cognitive:{turn}:{number}",
        metadata={"cognitive_turn_id":turn},
    )


def test_stop_before_first_segment_is_durable(pg):
    assert stop.cancel_guarded(
        pg, "turn-a", NOW, speech_only=True, reason="barge_in"
    ) == 0
    assert stop.stop_exists(pg, "turn-a")
    assert stop.enqueue_guarded(pg, sample(1))
    item = voice.get_utterance(pg, UUID(int=1))
    assert item.status == UtteranceStatus.CANCELLED
    assert item.metadata["voice_cancelled_turn"] == "turn-a"
    assert item.metadata["cancel_reason"] == "barge_in"
    assert voice.claim_next(pg, "PHONE", NOW) is None


def test_stop_after_enqueue_cancels_and_future_segments_stay_cancelled(pg):
    assert stop.enqueue_guarded(pg, sample(11))
    assert stop.cancel_guarded(pg, "turn-a", NOW, speech_only=True) == 1
    assert voice.get_utterance(pg, UUID(int=11)).status == UtteranceStatus.CANCELLED
    assert stop.enqueue_guarded(pg, sample(12))
    assert voice.get_utterance(pg, UUID(int=12)).status == UtteranceStatus.CANCELLED
    assert stop.stop_exists(pg, "turn-a")
    # Stop is per turn; an unrelated turn remains queueable.
    assert stop.enqueue_guarded(pg, sample(13, "turn-b"))
    assert voice.get_utterance(pg, UUID(int=13)).status == UtteranceStatus.QUEUED
    assert not stop.stop_exists(pg, "turn-b")


def test_concurrent_stop_and_enqueue_never_resurrect(pg):
    rendezvous = Barrier(2)

    def worker(kind):
        with connect() as conn:
            rendezvous.wait()
            if kind == "stop":
                return stop.cancel_guarded(
                    conn, "turn-race", NOW, speech_only=True,
                )
            return stop.enqueue_guarded(conn, sample(21, "turn-race"))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, ("stop", "enqueue")))
    assert results[1] is True
    assert results[0] in (0, 1)
    assert stop.stop_exists(pg, "turn-race")
    assert voice.get_utterance(pg, UUID(int=21)).status == UtteranceStatus.CANCELLED
    assert voice.claim_next(pg, "PHONE", NOW) is None


def test_stop_marker_persists_across_process_connection(pg):
    stop.cancel_guarded(pg, "turn-restart", NOW, speech_only=True)
    with connect() as fresh:
        assert stop.stop_exists(fresh, "turn-restart")
        assert stop.enqueue_guarded(fresh, sample(31, "turn-restart"))
    assert voice.get_utterance(pg, UUID(int=31)).status == UtteranceStatus.CANCELLED


def test_non_voice_stop_does_not_cancel_later_segment(pg):
    assert stop.cancel_guarded(
        pg, "turn-regular", NOW, speech_only=False
    ) == 0
    assert not stop.stop_exists(pg, "turn-regular")
    assert stop.enqueue_guarded(pg, sample(41, "turn-regular"))
    assert voice.get_utterance(pg, UUID(int=41)).status == UtteranceStatus.QUEUED


def test_failed_tombstone_update_rolls_back_cancelled_speech(pg):
    assert stop.enqueue_guarded(pg, sample(51, "turn-rollback"))
    pg.execute("""
        ALTER TABLE amora_pg_v18_lab.speech_turn_stops
        ADD CONSTRAINT lab_reject_stop CHECK(cancelled=0)
    """)
    psycopg = pytest.importorskip("psycopg")
    with pytest.raises(psycopg.errors.CheckViolation):
        stop.cancel_guarded(
            pg, "turn-rollback", NOW, speech_only=True
        )
    assert not stop.stop_exists(pg, "turn-rollback")
    assert voice.get_utterance(pg, UUID(int=51)).status == UtteranceStatus.QUEUED


def test_remote_database_rejected_before_sql():
    class Remote:
        info = SimpleNamespace(
            host="service.aivencloud.com",
            dbname="amora_pg_synthetic", server_version=180000,
        )
        def execute(self, *_a, **_kw):
            raise AssertionError("no remote SQL")
        def transaction(self):
            raise AssertionError("no remote transaction")
    with pytest.raises(ValueError, match="POSTGRES_LAB_LOCAL_DB_REQUIRED"):
        stop.install_stop_table(Remote())
    with pytest.raises(ValueError, match="POSTGRES_LAB_LOCAL_DB_REQUIRED"):
        stop.cancel_guarded(Remote(), "turn-a", NOW, speech_only=True)
