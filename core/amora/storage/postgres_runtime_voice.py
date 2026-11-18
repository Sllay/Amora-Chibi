"""Reviewed PG18 voice-only turn cancellation/tombstone SQL.

This contract strengthens cross-worker voice-stop behavior beyond the SQLite
implementation. It requires an explicitly installed, audited extra table.
No table is created by Core bootstrap or by this registry.
"""
from __future__ import annotations
from .postgres_runtime_connection import ReviewedStatement

VOICE_READY = "PG_VOICE_STOP_TABLE_READY"
VOICE_INSERT_TURN = "PG_VOICE_INSERT_TURN"
VOICE_LOCK_TURN = "PG_VOICE_LOCK_TURN"
VOICE_STOP_TURN = "PG_VOICE_STOP_TURN"


def voice_stop_statements() -> dict[str, ReviewedStatement]:
    return {
        VOICE_READY: ReviewedStatement(
            "SELECT to_regclass('amora_pg_v18_lab.speech_turn_stops') IS NOT NULL",
            0, 1,
        ),
        VOICE_INSERT_TURN: ReviewedStatement(
            """INSERT INTO amora_pg_v18_lab.speech_turn_stops(turn_id)
               VALUES (%s) ON CONFLICT(turn_id) DO NOTHING""",
            1,
        ),
        VOICE_LOCK_TURN: ReviewedStatement(
            """SELECT cancelled,reason,cancelled_at
               FROM amora_pg_v18_lab.speech_turn_stops
               WHERE turn_id=%s FOR UPDATE""",
            1, 1,
        ),
        VOICE_STOP_TURN: ReviewedStatement(
            """UPDATE amora_pg_v18_lab.speech_turn_stops
               SET cancelled=1,reason=%s,cancelled_at=%s
               WHERE turn_id=%s""",
            3,
        ),
    }
