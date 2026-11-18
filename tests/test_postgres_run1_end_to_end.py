"""Run 1: exercise original Core stores through the injected PG18 candidate.

Only disposable loopback PostgreSQL with invented v18 records. This is a
cross-store smoke test, NOT authorization for Aiven, main or Discord cutover.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import UUID

import pytest

from amora.conversation.models import (
    SpeechUtterance, TurnDecision, TurnSignal, TurnState, UtteranceStatus,
)
from amora.conversation.postgres_store import PostgresConversationStore
from amora.storage.database import AmoraDatabase
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Requires disposable PG18 loopback, never Aiven or production data",
)

NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def _raw_connection():
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )


@pytest.fixture()
def synthetic_backend():
    with closing(_raw_connection()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            status = prepare_disposable_core_v18(manager)
            assert status["production_backend_switched"] is False
            backend = synthetic_core_backend(_raw_connection, max_connections=4)
            yield backend
            assert backend.opened_connections == 0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")


def _speech(n: int, turn: str) -> SpeechUtterance:
    return SpeechUtterance(
        id=UUID(int=n), text=f"synthetic speech {n}",
        device_id="PHONE", created_at=NOW,
        dedupe_key=f"speech:cognitive:{turn}:{n}",
        metadata={"cognitive_turn_id": turn},
    )


def test_bootstrap_turn_voice_stop_ack_and_restart(tmp_path: Path, synthetic_backend):
    """Use actual AmoraDatabase and ConversationStore, not laboratory substitutes."""
    path = tmp_path / "not-a-sqlite-source.db"
    clock = SimulatedClock(NOW)
    backend = synthetic_backend

    with closing(AmoraDatabase(path, backend=backend)) as database:
        assert database.migrate() == 0
        assert database.schema_version() == 18

    with closing(PostgresConversationStore(path, clock, backend=backend)) as store:
        decision = TurnDecision(
            id=UUID(int=21), state=TurnState.TAKE_TURN, score=0.8,
            signal=TurnSignal(speaker_id="PERSON_SYNTHETIC", direct_call=True),
            created_at=NOW, dedupe_key="synthetic:turn:21",
        )
        assert store.put_turn_decision(decision) is True
        assert store.put_turn_decision(decision) is False
        assert store.get_turn_by_dedupe(decision.dedupe_key).id == decision.id
        assert any(item.id == decision.id for item in store.list_turns())

        # Stop before the first audio segment must survive a fresh connection.
        assert store.cancel_cognitive_turn(
            "synthetic-stopped", speech_only=True, reason="barge_in",
        ) == 0
        assert store.enqueue(_speech(31, "synthetic-stopped")) is True
        assert store.get_utterance(UUID(int=31)).status == UtteranceStatus.CANCELLED

        # A different turn remains claimable and can be acknowledged.
        assert store.enqueue(_speech(32, "synthetic-live")) is True
        claim = store.claim_next("PHONE")
        assert claim is not None and claim.id == UUID(int=32)
        assert store.acknowledge(claim.id, spoken=True).status == UtteranceStatus.SPOKEN
        assert store.pending_count() == 0
        assert store.spoken_count() >= 1

    with closing(PostgresConversationStore(path, clock, backend=backend)) as restarted:
        assert restarted.get_turn_by_dedupe("synthetic:turn:21").id == UUID(int=21)
        assert restarted.get_utterance(UUID(int=31)).status == UtteranceStatus.CANCELLED
        assert restarted.get_utterance(UUID(int=32)).status == UtteranceStatus.SPOKEN
        assert restarted.enqueue(_speech(33, "synthetic-stopped")) is True
        assert restarted.get_utterance(UUID(int=33)).status == UtteranceStatus.CANCELLED
        assert restarted.claim_next("PHONE") is None

    assert backend.opened_connections == 0
