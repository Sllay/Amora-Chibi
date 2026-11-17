"""Phase 2E.5: durable, race-safe voice-only turn stops (PG18 lab).

The current SQLite ConversationStore scans cancelled utterances for a stop
marker; it cannot protect a turn with no previous segments from a concurrent
new enqueue. The experimental PG18 turn tombstone serializes enqueue/cancel
through a single row lock, without changing Core's production behavior.

This is a *proposed semantic strengthening*, not SQLite behavior parity.
Only disposable loopback PG18 amora_pg_synthetic and synthetic models.
"""
from __future__ import annotations

import re
from datetime import datetime
from uuid import UUID

from amora.conversation.models import SpeechUtterance, UtteranceStatus

from .postgres_memory_lab import ensure_local_lab
from .postgres_speech_leases_lab import enqueue, cancel_cognitive_turn


class VoiceStopLabError(ValueError):
    pass


_SAFE_TURN = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")


def _turn(value: UUID | str) -> str:
    result = str(value)
    if _SAFE_TURN.fullmatch(result) is None:
        raise VoiceStopLabError("VOICE_STOP_INVALID_TURN_ID")
    return result


def _time(now: datetime) -> str:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise VoiceStopLabError("VOICE_STOP_AWARE_TIME_REQUIRED")
    return now.isoformat()


def install_stop_table(conn) -> None:
    """Extra isolated lab schema object, not one of the canonical 51."""
    ensure_local_lab(conn)
    with conn.transaction():
        conn.execute("""
            CREATE TABLE amora_pg_v18_lab.speech_turn_stops (
                turn_id TEXT PRIMARY KEY,
                cancelled BIGINT NOT NULL DEFAULT 0 CHECK(cancelled IN (0,1)),
                reason TEXT NOT NULL DEFAULT '',
                cancelled_at TEXT
            )
        """)


def _lock_turn(conn, turn: str):
    # The insert-on-conflict synchronizes creation of the same turn row.
    # SELECT FOR UPDATE then serializes cancellation and enqueue.
    conn.execute("""
        INSERT INTO amora_pg_v18_lab.speech_turn_stops(turn_id)
        VALUES (%s) ON CONFLICT(turn_id) DO NOTHING
    """, (turn,))
    state = conn.execute("""
        SELECT cancelled,reason,cancelled_at
        FROM amora_pg_v18_lab.speech_turn_stops
        WHERE turn_id=%s FOR UPDATE
    """, (turn,)).fetchone()
    if state is None:
        raise VoiceStopLabError("VOICE_STOP_LOCK_MISSING")
    return state


def enqueue_guarded(conn, item: SpeechUtterance) -> bool:
    ensure_local_lab(conn)
    if not isinstance(item, SpeechUtterance):
        raise VoiceStopLabError("VOICE_STOP_INVALID_UTTERANCE")
    turn_id = item.metadata.get("cognitive_turn_id")
    if not turn_id:
        return enqueue(conn, item)
    turn = _turn(turn_id)
    with conn.transaction():
        stopped, reason, _ = _lock_turn(conn, turn)
        if stopped:
            item.status = UtteranceStatus.CANCELLED
            item.metadata["voice_cancelled_turn"] = turn
            item.metadata["cancel_reason"] = reason or "voice_cancelled"
        # The underlying enqueue uses a nested psycopg transaction savepoint.
        # Outer row lock must remain held through the final insert/commit.
        return enqueue(conn, item)


def cancel_guarded(conn, turn_id: UUID | str, now: datetime, *,
                   reason: str = "barge_in", speech_only: bool = False) -> int:
    ensure_local_lab(conn)
    turn = _turn(turn_id)
    stamp = _time(now)
    if not isinstance(reason, str):
        raise VoiceStopLabError("VOICE_STOP_INVALID_REASON")
    bounded_reason = reason[:96]
    with conn.transaction():
        _lock_turn(conn, turn)
        changed = cancel_cognitive_turn(
            conn, turn, now, reason=bounded_reason, speech_only=speech_only,
        )
        if speech_only:
            conn.execute("""
                UPDATE amora_pg_v18_lab.speech_turn_stops
                SET cancelled=1,reason=%s,cancelled_at=%s
                WHERE turn_id=%s
            """, (bounded_reason, stamp, turn))
        return changed


def stop_exists(conn, turn_id: UUID | str) -> bool:
    """Lab-only state metric; not an identity authorization endpoint."""
    ensure_local_lab(conn)
    turn = _turn(turn_id)
    row = conn.execute("""
        SELECT cancelled FROM amora_pg_v18_lab.speech_turn_stops
        WHERE turn_id=%s
    """, (turn,)).fetchone()
    return bool(row and row[0])
