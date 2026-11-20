"""PostgreSQL conversation store with durable cross-worker voice-stop barrier.

Extends canonical ConversationStore and retains the same public method
signatures/models. Uses the reviewed SQL registry for all inherited methods.
Adds a durable per-turn lock so stop-before-first-segment cannot revive speech.

Only for injected PostgreSQL candidate with an explicitly installed
speech_turn_stops table. It is never selected by production environment.
"""
from __future__ import annotations

import re
from pathlib import Path
from uuid import UUID

from amora.storage.backend import StorageBackend
from amora.storage.postgres_runtime_connection import PostgresRuntimeError
from amora.storage.postgres_runtime_voice import (
    VOICE_READY, VOICE_INSERT_TURN, VOICE_LOCK_TURN, VOICE_STOP_TURN,
)
from amora.time import Clock

from .models import SpeechUtterance, UtteranceStatus
from .store import ConversationStore

_TURN_ID = re.compile(r"[A-Za-z0-9._-]{1,128}\Z")


class PostgresConversationStore(ConversationStore):
    def __init__(self, path: Path, clock: Clock, *, backend: StorageBackend):
        if backend.capabilities.name != "postgres-candidate":
            raise PostgresRuntimeError("PG_VOICE_BACKEND_REQUIRED")
        super().__init__(path, clock, backend=backend)
        try:
            ready = self._conn.execute(VOICE_READY).fetchone()
            if ready is None or ready[0] is not True:
                raise PostgresRuntimeError("PG_VOICE_STOP_TABLE_NOT_PREPARED")
        except BaseException:
            self.close()
            raise

    @staticmethod
    def _turn(value: UUID | str) -> str:
        turn = str(value)
        if _TURN_ID.fullmatch(turn) is None:
            raise PostgresRuntimeError("PG_VOICE_INVALID_TURN")
        return turn

    def _lock_turn(self, turn: str):
        self._conn.execute(VOICE_INSERT_TURN,(turn,))
        state = self._conn.execute(VOICE_LOCK_TURN,(turn,)).fetchone()
        if state is None:
            raise PostgresRuntimeError("PG_VOICE_TURN_LOCK_MISSING")
        return state

    def enqueue(self, item: SpeechUtterance) -> bool:
        original_turn = item.metadata.get("cognitive_turn_id")
        if not original_turn:
            return super().enqueue(item)
        turn = self._turn(original_turn)
        with self._lock, self._conn:
            state = self._lock_turn(turn)
            if bool(state["cancelled"]):
                item.status = UtteranceStatus.CANCELLED
                item.metadata["voice_cancelled_turn"] = turn
                item.metadata["cancel_reason"] = state["reason"] or "voice_cancelled"
            # Parent inserts inside a nested transaction/savepoint while the
            # durable turn lock remains held by the outer transaction.
            return super().enqueue(item)

    def cancel_cognitive_turn(
        self, turn_id: UUID | str, *,
        reason: str = "barge_in", speech_only: bool = False,
    ) -> int:
        turn = self._turn(turn_id)
        with self._lock, self._conn:
            self._lock_turn(turn)
            if speech_only:
                self._conn.execute(
                    VOICE_STOP_TURN,(
                        str(reason)[:96],self.clock.now().isoformat(),turn,
                    ),
                )
            return super().cancel_cognitive_turn(
                turn,reason=reason,speech_only=speech_only,
            )
