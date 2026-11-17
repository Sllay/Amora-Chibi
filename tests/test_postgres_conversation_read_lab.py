"""Execute ORIGINAL ConversationStore reads against SQLite v18 and PG18.

The bridge implements only eight explicit SELECT statements. All records are
synthetic. Tests must fail if the canonical store changes its SQL or tries to
write through the bridge. No production backend or external PostgreSQL.
"""
from __future__ import annotations

from datetime import datetime, timezone
from threading import RLock
from types import SimpleNamespace
from uuid import UUID

import os
import sqlite3
import pytest

from amora.conversation.models import (
    SpeechUtterance, TurnDecision, TurnSignal, TurnState, UtteranceStatus,
)
from amora.conversation.store import ConversationStore
from amora.storage.backend import SQLiteBackend
from amora.storage import postgres_domain_parity_lab as domain
from amora.storage import postgres_speech_leases_lab as voice
from amora.storage import postgres_v18_lab_schema as schema
from amora.storage import postgres_v18_manifest as manifest
from amora.storage.postgres_conversation_read_lab import (
    ConversationReadLabError, PostgresConversationReadLab,
    approved_query_count,
)
from amora.storage.postgres_memory_lab import ensure_local_lab

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Disposable loopback PG18 is mandatory",
)

NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def connect():
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )


def store(connection, *, sqlite: bool) -> ConversationStore:
    result = ConversationStore.__new__(ConversationStore)
    result._conn = connection
    result._lock = RLock()
    result.backend = SQLiteBackend() if sqlite else None
    return result


@pytest.fixture
def pair():
    raw = connect()
    sqlite = manifest.synthetic_v18_connection()
    sqlite.row_factory = sqlite3.Row
    try:
        ensure_local_lab(raw)
        raw.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        schema.install_pg_v18_lab(raw)
        yield store(sqlite, sqlite=True), store(
            PostgresConversationReadLab(raw), sqlite=False
        ), raw
    finally:
        sqlite.close()
        if not raw.closed:
            raw.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
            raw.close()


def turn(n: int, dedupe: str):
    return TurnDecision(
        id=UUID(int=n), state=TurnState.TAKE_TURN,
        score=0.8, created_at=NOW,
        signal=TurnSignal(
            speaker_id="PERSON_SYNTHETIC",
            direct_call=True,
            active_conversation=True,
        ),
        dedupe_key=dedupe,
    )


def speech(n: int, *, dedupe=None):
    return SpeechUtterance(
        id=UUID(int=n), text=f"mensagem sintética {n}",
        device_id="PHONE", created_at=NOW,
        dedupe_key=dedupe or f"synthetic:speech:{n}",
        metadata={"cognitive_turn_id":"synthetic-turn"},
    )


def mirror_turn(sqlite_store, pg, item: TurnDecision):
    import json
    assert sqlite_store.put_turn_decision(item) is True
    payload = json.dumps(item.model_dump(mode="json"), ensure_ascii=False,
                         separators=(",", ":"))
    assert domain.put_turn_decision(
        pg, decision_id=str(item.id), state=item.state.value,
        score=item.score, payload_json=payload,
        created_at=item.created_at.isoformat(), dedupe_key=item.dedupe_key,
    ) is True


def mirror_speech(sqlite_store, pg, item: SpeechUtterance):
    assert sqlite_store.enqueue(item.model_copy(deep=True)) is True
    assert voice.enqueue(pg, item.model_copy(deep=True)) is True


def same_model(left, right):
    assert (left is None) == (right is None)
    if left is not None:
        assert left.model_dump(mode="json") == right.model_dump(mode="json")


def test_actual_turn_read_methods_use_exact_postgres_query_mappings(pair):
    sqlite_store, pg_store, pg = pair
    assert approved_query_count() == 8
    for n in (1, 2, 3):
        mirror_turn(sqlite_store, pg, turn(n, f"synthetic:turn:{n}"))
    same_model(
        sqlite_store.get_turn_by_dedupe("synthetic:turn:1"),
        pg_store.get_turn_by_dedupe("synthetic:turn:1"),
    )
    same_model(
        sqlite_store.get_turn_by_dedupe("unknown"),
        pg_store.get_turn_by_dedupe("unknown"),
    )
    assert sqlite_store.turn_count() == pg_store.turn_count() == 3
    for limit in (1, 2, 50, 400):
        left = [item.model_dump(mode="json") for item in sqlite_store.list_turns(limit)]
        right = [item.model_dump(mode="json") for item in pg_store.list_turns(limit)]
        assert left == right


def test_actual_utterance_read_count_methods_and_reconnect(pair):
    sqlite_store, pg_store, pg = pair
    for n in (20, 21, 22):
        mirror_speech(sqlite_store, pg, speech(n))
    same_model(
        sqlite_store.get_utterance_by_dedupe("synthetic:speech:20"),
        pg_store.get_utterance_by_dedupe("synthetic:speech:20"),
    )
    same_model(
        sqlite_store.get_utterance(UUID(int=21)),
        pg_store.get_utterance(UUID(int=21)),
    )
    same_model(
        sqlite_store.get_utterance("missing"),
        pg_store.get_utterance("missing"),
    )
    assert sqlite_store.pending_count() == pg_store.pending_count() == 3
    assert sqlite_store.spoken_count() == pg_store.spoken_count() == 0
    from datetime import timedelta
    clock = SimpleNamespace(now=lambda:NOW)
    sqlite_store.clock = clock
    sqlite_store.acknowledge(UUID(int=20), spoken=True)
    voice.acknowledge(pg, UUID(int=20), NOW, spoken=True)
    assert sqlite_store.pending_count() == pg_store.pending_count() == 2
    assert sqlite_store.spoken_count() == pg_store.spoken_count() == 1
    for limit in (1, 2, 50):
        left = [x.model_dump(mode="json") for x in sqlite_store.list_utterances(limit)]
        right = [x.model_dump(mode="json") for x in pg_store.list_utterances(limit)]
        assert left == right
    with connect() as new_connection:
        restarted = store(PostgresConversationReadLab(new_connection), sqlite=False)
        same_model(
            sqlite_store.get_utterance(UUID(int=20)),
            restarted.get_utterance(UUID(int=20)),
        )


def test_original_conversation_store_writes_fail_closed(pair):
    _, pg_store, pg = pair
    item = speech(50)
    with pytest.raises(ConversationReadLabError, match="CONVERSATION_LAB_READ_ONLY"):
        pg_store.enqueue(item)
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.speech_utterances
    """).fetchone() == (0,)


def test_unknown_sql_arity_and_injection_are_rejected(pair):
    _, pg_store, _ = pair
    bridge = pg_store._conn
    with pytest.raises(ConversationReadLabError, match="CONVERSATION_LAB_SQL_NOT_REVIEWED"):
        bridge.execute("SELECT * FROM speech_utterances")
    with pytest.raises(ConversationReadLabError, match="CONVERSATION_LAB_SQL_NOT_REVIEWED"):
        bridge.execute("DELETE FROM speech_utterances")
    with pytest.raises(ConversationReadLabError, match="CONVERSATION_LAB_SQL_NOT_REVIEWED"):
        bridge.execute("SELECT utterance_json FROM speech_utterances WHERE id=?; DROP TABLE speech_utterances")
    with pytest.raises(ConversationReadLabError, match="CONVERSATION_LAB_PARAMETER_COUNT"):
        bridge.execute("SELECT utterance_json FROM speech_utterances WHERE id = ?")
    with pytest.raises(ConversationReadLabError, match="CONVERSATION_LAB_PARAMETER_TYPE"):
        bridge.execute("SELECT utterance_json FROM speech_utterances WHERE id = ?", (42,))
    with pytest.raises(ConversationReadLabError, match="CONVERSATION_LAB_LIMIT_INVALID"):
        bridge.execute(
            "SELECT utterance_json FROM speech_utterances ORDER BY created_at DESC LIMIT ?",
            (999,),
        )
    assert bridge.execute(
        "SELECT utterance_json FROM speech_utterances WHERE id = ?",
        ("a' OR TRUE --",),
    ).fetchone() is None
    with pytest.raises(ConversationReadLabError, match="CONVERSATION_LAB_READ_ONLY"):
        bridge.executemany("INSERT INTO speech_utterances VALUES (?)", [])


def test_refuses_any_nonlocal_database():
    class Remote:
        info = SimpleNamespace(
            host="private.aivencloud.com",
            dbname="amora_pg_synthetic",
            server_version=180000,
        )
        autocommit = True
        closed = False
        def execute(self, *_a, **_kw):
            raise AssertionError("no external SQL is allowed")
    with pytest.raises(ValueError, match="POSTGRES_LAB_LOCAL_DB_REQUIRED"):
        PostgresConversationReadLab(Remote())
