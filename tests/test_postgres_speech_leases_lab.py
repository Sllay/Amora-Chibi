"""Real PG18 / canonical SQLite ConversationStore speech parity tests.

Runs complete SpeechUtterance Pydantic models with invented data only.
Includes a second independent PostgreSQL connection race test.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from threading import RLock
from uuid import UUID

import os
import sqlite3
import pytest

from amora.conversation.models import SpeechUtterance, UtteranceStatus
from amora.conversation.store import ConversationStore
from amora.storage import postgres_speech_leases_lab as voice
from amora.storage import postgres_v18_manifest as v18
from amora.storage import postgres_v18_lab_schema as pg_schema
from amora.storage.backend import SQLiteBackend
from amora.storage.postgres_memory_lab import ensure_local_lab

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Disposable PostgreSQL 18 loopback service required",
)
NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def connect():
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )


class FixtureClock:
    def __init__(self):
        self.current = NOW

    def now(self):
        return self.current


@pytest.fixture
def pair():
    pg = connect()
    sql = v18.synthetic_v18_connection()
    sql.row_factory = sqlite3.Row
    clock = FixtureClock()
    store = ConversationStore.__new__(ConversationStore)
    store._conn = sql
    store._lock = RLock()
    store.backend = SQLiteBackend()
    store.clock = clock
    try:
        ensure_local_lab(pg)
        pg.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        pg_schema.install_pg_v18_lab(pg)
        yield store, pg, clock
    finally:
        sql.close()
        pg.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        pg.close()


def sample(n, *, turn="turn-a", device="PHONE", priority=50, dedupe=None):
    return SpeechUtterance(
        id=UUID(int=n), text=f"Mensagem sintética {n}",
        device_id=device, priority=priority, created_at=NOW,
        dedupe_key=dedupe or f"speech:cognitive:{turn}:{n}",
        metadata={"cognitive_turn_id": turn},
    )


def equal(left, right):
    assert (left is None) == (right is None)
    if left is not None:
        assert left.model_dump(mode="json") == right.model_dump(mode="json")


def test_claim_priority_ack_requeue_cancel_and_restart(pair):
    sqlite, pg, clock = pair
    for item in (
        sample(1, priority=10), sample(2, priority=90),
        sample(3, priority=50, turn="turn-b"),
    ):
        assert sqlite.enqueue(item.model_copy(deep=True))
        assert voice.enqueue(pg, item.model_copy(deep=True))
    left = sqlite.claim_next("PHONE")
    right = voice.claim_next(pg, "PHONE", clock.now())
    equal(left, right)
    assert left.id == UUID(int=2)
    assert left.claim_expires_at == NOW + timedelta(seconds=30)
    equal(sqlite.acknowledge(left.id, spoken=False),
          voice.acknowledge(pg, left.id, clock.now(), spoken=False))
    left = sqlite.claim_next("PHONE", cognitive_turn_id="turn-b")
    right = voice.claim_next(
        pg, "PHONE", clock.now(), cognitive_turn_id="turn-b"
    )
    equal(left, right)
    assert left.id == UUID(int=3)
    equal(sqlite.acknowledge(left.id, spoken=True),
          voice.acknowledge(pg, left.id, clock.now(), spoken=True))
    assert sqlite.acknowledge(left.id, spoken=False).status == UtteranceStatus.SPOKEN
    assert voice.acknowledge(
        pg, left.id, clock.now(), spoken=False
    ).status == UtteranceStatus.SPOKEN
    equal(sqlite.cancel(left.id, reason="no"),
          voice.cancel(pg, left.id, clock.now(), reason="no"))
    with connect() as restarted:
        equal(sqlite.get_utterance(left.id), voice.get_utterance(restarted, left.id))


def test_lease_expiry_reclaims_after_worker_crash(pair):
    sqlite, pg, clock = pair
    item = sample(11, priority=45)
    assert sqlite.enqueue(item.model_copy(deep=True))
    assert voice.enqueue(pg, item.model_copy(deep=True))
    left = sqlite.claim_next("PHONE", lease_seconds=2)
    right = voice.claim_next(pg, "PHONE", clock.now(), lease_seconds=2)
    equal(left, right)
    assert left.claim_expires_at == NOW + timedelta(seconds=5)
    clock.current += timedelta(seconds=6)
    left = sqlite.claim_next("PHONE")
    right = voice.claim_next(pg, "PHONE", clock.now())
    equal(left, right)
    assert left.id == item.id
    assert left.claimed_at == clock.now()


def test_voice_only_cancel_does_not_resurrect_later_segment(pair):
    sqlite, pg, clock = pair
    first = sample(21, turn="turn-stop")
    other = sample(22, turn="turn-continue")
    for item in (first, other):
        assert sqlite.enqueue(item.model_copy(deep=True))
        assert voice.enqueue(pg, item.model_copy(deep=True))
    assert sqlite.cancel_cognitive_turn(
        "turn-stop", reason="barge_in", speech_only=True
    ) == voice.cancel_cognitive_turn(
        pg, "turn-stop", clock.now(), reason="barge_in", speech_only=True
    ) == 1
    later = sample(23, turn="turn-stop")
    assert sqlite.enqueue(later.model_copy(deep=True))
    assert voice.enqueue(pg, later.model_copy(deep=True))
    equal(sqlite.get_utterance(later.id), voice.get_utterance(pg, later.id))
    assert voice.get_utterance(pg, later.id).status == UtteranceStatus.CANCELLED
    assert voice.get_utterance(pg, other.id).status == UtteranceStatus.QUEUED
    assert sqlite.cancel_cognitive_turn(
        "turn-stop", speech_only=True
    ) == voice.cancel_cognitive_turn(
        pg, "turn-stop", clock.now(), speech_only=True
    ) == 0


def test_duplicate_enqueue_and_device_isolation(pair):
    sqlite, pg, clock = pair
    first = sample(31, device="MOBILE", priority=5)
    assert sqlite.enqueue(first.model_copy(deep=True))
    assert voice.enqueue(pg, first.model_copy(deep=True))
    duplicate = sample(32, device="MOBILE", dedupe=first.dedupe_key)
    assert sqlite.enqueue(duplicate.model_copy(deep=True)) is False
    assert voice.enqueue(pg, duplicate.model_copy(deep=True)) is False
    assert sqlite.claim_next("OTHER") is None
    assert voice.claim_next(pg, "OTHER", clock.now()) is None
    equal(sqlite.get_utterance(first.id), voice.get_utterance(pg, first.id))


def test_concurrent_workers_cannot_double_claim_one_utterance(pair):
    _, pg, clock = pair
    item = sample(41)
    assert voice.enqueue(pg, item)

    def worker(_index):
        with connect() as connection:
            got = voice.claim_next(connection, "PHONE", clock.now())
            return str(got.id) if got else None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(worker, (0, 1)))
    assert results.count(str(item.id)) == 1
    assert results.count(None) == 1
    assert voice.get_utterance(pg, item.id).status == UtteranceStatus.CLAIMED


def test_sql_failure_rolls_back_claim_lease_and_json(pair):
    _, pg, clock = pair
    item = sample(51)
    assert voice.enqueue(pg, item)
    pg.execute("""
        ALTER TABLE amora_pg_v18_lab.speech_utterances
        ADD CONSTRAINT disallow_claimed_fixture CHECK(status<>'claimed')
    """)
    psycopg = pytest.importorskip("psycopg")
    with pytest.raises(psycopg.errors.CheckViolation):
        voice.claim_next(pg, "PHONE", clock.now())
    row = pg.execute("""
        SELECT status,claimed_at,claim_expires_at,utterance_json
        FROM amora_pg_v18_lab.speech_utterances WHERE id=%s
    """, (str(item.id),)).fetchone()
    assert row[:3] == ("queued", None, None)
    assert SpeechUtterance.model_validate_json(row[3]).status == UtteranceStatus.QUEUED


def test_refuses_nonlocal_pg_before_all_sql():
    from types import SimpleNamespace

    class Remote:
        info = SimpleNamespace(
            host="amora.aivencloud.com", dbname="amora_pg_synthetic",
            server_version=180000,
        )

        def execute(self, *_args, **_kwargs):
            raise AssertionError("no SQL allowed")

        def transaction(self):
            raise AssertionError("no transaction allowed")

    with pytest.raises(ValueError, match="POSTGRES_LAB_LOCAL_DB_REQUIRED"):
        voice.claim_next(Remote(), "PHONE", NOW)
