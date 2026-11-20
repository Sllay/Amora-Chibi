"""Actual disposable PostgreSQL18 evidence for Memory V2 calendar recall.

Never connects to Aiven; only CI loopback synthetic lab with opt-in env.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone, timedelta
import os
from pathlib import Path
from uuid import uuid4

import pytest

from amora.cognition.models import ConversationMessage, MessageSource, MessageStatus
from amora.cognition.postgres_store import PostgresCognitiveStore
from amora.events import PrivacyScope
from amora.memory.recall_intent import topic_terms
from amora.messaging.session_identity import messaging_session_ref
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Disposable PostgreSQL18 lab only",
)

STAMP = datetime(2026, 11, 11, 14, 15, tzinfo=timezone(timedelta(hours=-3)))


def _raw():
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )


def test_pg_historical_owner_date_query_survives_restart(tmp_path: Path):
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend = synthetic_core_backend(_raw, max_connections=2)
            path = tmp_path / "not-sqlite.db"
            clock = SimulatedClock(STAMP)
            turn = uuid4()
            session = messaging_session_ref("discord", "primary", "owner-dm")
            message = ConversationMessage(
                turn_id=turn, speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA",
                source=MessageSource.TEXT, status=MessageStatus.RECEIVED,
                raw_text="Projeto Orquídea: cinco mapas de sobrevivência",
                started_at=STAMP.astimezone(timezone.utc),
                privacy_scope=PrivacyScope.PERSONAL,
                remote_processing_allowed=True, session_id=session,
                metadata={
                    "surface": "platform_messaging", "platform": "discord",
                    "channel_type": "dm", "platform_sent_at": STAMP.isoformat(),
                    "native_message_id_hash": "a" * 20, "transport_revision": 1,
                },
                dedupe_key=f"cognitive-input:{turn}",
            )
            with closing(PostgresCognitiveStore(path, clock, backend=backend)) as store:
                assert store.put_message(message) is True
            with closing(PostgresCognitiveStore(path, clock, backend=backend)) as reopened:
                start = datetime(2026, 11, 11, 3, tzinfo=timezone.utc)
                end = start + timedelta(days=1)
                rows, complete = reopened.owner_day_conversations(
                    "PERSON_BK", start, end, terms=topic_terms("Orquídea"),
                )
                assert complete and len(rows) == 1
                assert rows[0]["user_text"].startswith("Projeto Orquídea")
                assert rows[0]["reply_text"] is None
                assert rows[0]["at"] == STAMP.astimezone(timezone.utc)
                episode = reopened.owner_turn_conversations("PERSON_BK", [str(turn)])
                assert list(episode) == [str(turn)]
                assert episode[str(turn)]["user_text"] == message.raw_text
                assert episode[str(turn)]["reply_text"] is None
                assert not reopened.owner_turn_conversations("OTHER_OWNER", [str(turn)])
                assert reopened.owner_day_conversations(
                    "OTHER_OWNER", start, end,
                )[0] == []
                assert reopened.owner_day_conversations(
                    "PERSON_BK", start + timedelta(days=1), end + timedelta(days=1),
                )[0] == []
            assert backend.opened_connections == 0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
