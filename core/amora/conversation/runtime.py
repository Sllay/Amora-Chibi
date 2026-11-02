from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from uuid import uuid4

from amora.time import Clock


class SessionState(StrEnum):
    ACTIVE = "active"
    IDLE = "idle"
    DORMANT = "dormant"
    CLOSED = "closed"


class AttentionState(StrEnum):
    DIRECT = "direct"
    ENGAGED = "engaged"
    MONITORING = "monitoring"
    BACKGROUND = "background"
    DISENGAGED = "disengaged"


class RuntimeTurnState(StrEnum):
    RECEIVED = "received"
    ACCEPTED = "accepted"
    THINKING = "thinking"
    READY = "ready"
    DELIVERING = "delivering"
    DELIVERED = "delivered"
    CANCELLED = "cancelled"
    SUPERSEDED = "superseded"
    FAILED = "failed"
    RECOVERING = "recovering"


_FINAL_TURN_STATES = frozenset(
    {
        RuntimeTurnState.DELIVERED,
        RuntimeTurnState.CANCELLED,
        RuntimeTurnState.SUPERSEDED,
        RuntimeTurnState.FAILED,
    }
)


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    session_id: str
    platform: str
    account_scope: str
    conversation_id: str
    state: SessionState
    attention: AttentionState
    revision: int
    floor_owner_ref: str | None
    floor_owner_entity_id: str | None
    opened_at: datetime
    last_activity_at: datetime
    idle_at: datetime | None
    dormant_at: datetime | None
    closed_at: datetime | None


@dataclass(frozen=True, slots=True)
class TurnSnapshot:
    turn_id: str
    session_id: str
    source_message_id: str
    source_native_message_id: str
    speaker_actor_ref: str
    speaker_entity_id: str | None
    state: RuntimeTurnState
    revision: int
    created_at: datetime
    updated_at: datetime
    reply_valid_until: datetime | None
    cancellation_reason: str | None


class ConversationRuntime:
    """Persistent transport-neutral conversation state.

    Session lifetime and turn-delivery authority are intentionally separate.
    Ordinary cognition latency never invalidates an accepted/thinking turn.
    A delivery deadline begins only once a reply becomes READY.
    """

    ACTIVE_SECONDS = 180
    DORMANT_SECONDS = 900
    DELIVERY_SECONDS = 120

    def __init__(self, *, database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    @staticmethod
    def _key(
        platform: str, account_scope: str, conversation_id: str
    ) -> tuple[str, str, str]:
        return platform, account_scope, conversation_id

    def _session_row(self, key: tuple[str, str, str]):
        return self.database._conn.execute(
            """SELECT * FROM conversation_runtime_sessions
               WHERE platform=? AND account_scope=? AND conversation_id=?""",
            key,
        ).fetchone()

    @staticmethod
    def _session(row) -> SessionSnapshot | None:
        if row is None:
            return None
        return SessionSnapshot(
            session_id=str(row["session_id"]),
            platform=str(row["platform"]),
            account_scope=str(row["account_scope"]),
            conversation_id=str(row["conversation_id"]),
            state=SessionState(row["state"]),
            attention=AttentionState(row["attention_state"]),
            revision=int(row["revision"]),
            floor_owner_ref=(str(row["floor_owner_ref"]) if row["floor_owner_ref"] else None),
            floor_owner_entity_id=(
                str(row["floor_owner_entity_id"]) if row["floor_owner_entity_id"] else None
            ),
            opened_at=datetime.fromisoformat(row["opened_at"]),
            last_activity_at=datetime.fromisoformat(row["last_activity_at"]),
            idle_at=datetime.fromisoformat(row["idle_at"]) if row["idle_at"] else None,
            dormant_at=(
                datetime.fromisoformat(row["dormant_at"]) if row["dormant_at"] else None
            ),
            closed_at=(
                datetime.fromisoformat(row["closed_at"]) if row["closed_at"] else None
            ),
        )

    @staticmethod
    def _turn(row) -> TurnSnapshot | None:
        if row is None:
            return None
        return TurnSnapshot(
            turn_id=str(row["turn_id"]),
            session_id=str(row["session_id"]),
            source_message_id=str(row["source_message_id"]),
            source_native_message_id=str(row["source_native_message_id"]),
            speaker_actor_ref=str(row["speaker_actor_ref"]),
            speaker_entity_id=(str(row["speaker_entity_id"]) if row["speaker_entity_id"] else None),
            state=RuntimeTurnState(row["state"]),
            revision=int(row["revision"]),
            created_at=datetime.fromisoformat(row["created_at"]),
            updated_at=datetime.fromisoformat(row["updated_at"]),
            reply_valid_until=(
                datetime.fromisoformat(row["reply_valid_until"])
                if row["reply_valid_until"]
                else None
            ),
            cancellation_reason=(
                str(row["cancellation_reason"]) if row["cancellation_reason"] else None
            ),
        )

    def get(
        self, *, platform: str, account_scope: str, conversation_id: str
    ) -> SessionSnapshot | None:
        key = self._key(platform, account_scope, conversation_id)
        with self.database._lock:
            return self._session(self._session_row(key))

    def engage(
        self,
        *,
        platform: str,
        account_scope: str,
        conversation_id: str,
        actor_ref: str,
        speaker_entity_id: str | None,
        direct: bool,
    ) -> SessionSnapshot:
        key = self._key(platform, account_scope, conversation_id)
        now = self.clock.now()
        attention = AttentionState.DIRECT if direct else AttentionState.ENGAGED

        with self.database._lock, self.database._conn:
            row = self._session_row(key)
            if row is None:
                self.database._conn.execute(
                    """INSERT INTO conversation_runtime_sessions(
                           platform, account_scope, conversation_id, session_id,
                           state, attention_state, floor_owner_ref,
                           floor_owner_entity_id, opened_at, last_activity_at,
                           idle_at, dormant_at, closed_at, revision
                       ) VALUES (?, ?, ?, ?, 'active', ?, ?, ?, ?, ?, NULL, NULL, NULL, 1)""",
                    (
                        *key,
                        str(uuid4()),
                        attention.value,
                        actor_ref,
                        speaker_entity_id,
                        now.isoformat(),
                        now.isoformat(),
                    ),
                )
            elif SessionState(row["state"]) == SessionState.CLOSED:
                self.database._conn.execute(
                    """UPDATE conversation_runtime_sessions
                       SET session_id=?, state='active', attention_state=?,
                           floor_owner_ref=?, floor_owner_entity_id=?,
                           opened_at=?, last_activity_at=?, idle_at=NULL,
                           dormant_at=NULL, closed_at=NULL, revision=revision+1
                       WHERE platform=? AND account_scope=? AND conversation_id=?""",
                    (
                        str(uuid4()),
                        attention.value,
                        actor_ref,
                        speaker_entity_id,
                        now.isoformat(),
                        now.isoformat(),
                        *key,
                    ),
                )
            else:
                self.database._conn.execute(
                    """UPDATE conversation_runtime_sessions
                       SET state='active', attention_state=?,
                           floor_owner_ref=?, floor_owner_entity_id=?,
                           last_activity_at=?, idle_at=NULL, dormant_at=NULL,
                           revision=revision+1
                       WHERE platform=? AND account_scope=? AND conversation_id=?""",
                    (
                        attention.value,
                        actor_ref,
                        speaker_entity_id,
                        now.isoformat(),
                        *key,
                    ),
                )

            session = self._session(self._session_row(key))
        assert session is not None
        return session

    def shift_floor(
        self,
        *,
        platform: str,
        account_scope: str,
        conversation_id: str,
        actor_ref: str | None,
        speaker_entity_id: str | None,
    ) -> SessionSnapshot | None:
        key = self._key(platform, account_scope, conversation_id)
        now = self.clock.now()
        with self.database._lock, self.database._conn:
            row = self._session_row(key)
            if row is None or SessionState(row["state"]) == SessionState.CLOSED:
                return self._session(row)
            self.database._conn.execute(
                """UPDATE conversation_runtime_sessions
                   SET attention_state='background', floor_owner_ref=?,
                       floor_owner_entity_id=?, last_activity_at=?,
                       revision=revision+1
                   WHERE platform=? AND account_scope=? AND conversation_id=?""",
                (actor_ref, speaker_entity_id, now.isoformat(), *key),
            )
            return self._session(self._session_row(key))

    def close(
        self, *, platform: str, account_scope: str, conversation_id: str
    ) -> SessionSnapshot | None:
        key = self._key(platform, account_scope, conversation_id)
        now = self.clock.now().isoformat()
        with self.database._lock, self.database._conn:
            row = self._session_row(key)
            if row is None:
                return None
            self.database._conn.execute(
                """UPDATE conversation_runtime_sessions
                   SET state='closed', attention_state='disengaged',
                       floor_owner_ref=NULL, floor_owner_entity_id=NULL,
                       closed_at=?, last_activity_at=?, revision=revision+1
                   WHERE platform=? AND account_scope=? AND conversation_id=?""",
                (now, now, *key),
            )
            return self._session(self._session_row(key))

    def tick(
        self, *, platform: str, account_scope: str, conversation_id: str
    ) -> SessionSnapshot | None:
        key = self._key(platform, account_scope, conversation_id)
        now = self.clock.now()
        with self.database._lock, self.database._conn:
            session = self._session(self._session_row(key))
            if session is None or session.state == SessionState.CLOSED:
                return session

            age = (now - session.last_activity_at).total_seconds()
            if age >= self.DORMANT_SECONDS and session.state != SessionState.DORMANT:
                self.database._conn.execute(
                    """UPDATE conversation_runtime_sessions
                       SET state='dormant', attention_state='disengaged',
                           floor_owner_ref=NULL, floor_owner_entity_id=NULL,
                           dormant_at=?, revision=revision+1
                       WHERE platform=? AND account_scope=? AND conversation_id=?""",
                    (now.isoformat(), *key),
                )
            elif age >= self.ACTIVE_SECONDS and session.state == SessionState.ACTIVE:
                self.database._conn.execute(
                    """UPDATE conversation_runtime_sessions
                       SET state='idle', attention_state='monitoring',
                           idle_at=?, revision=revision+1
                       WHERE platform=? AND account_scope=? AND conversation_id=?""",
                    (now.isoformat(), *key),
                )
            return self._session(self._session_row(key))

    def accepts_continuity(
        self,
        *,
        platform: str,
        account_scope: str,
        conversation_id: str,
        actor_ref: str,
    ) -> bool:
        session = self.tick(
            platform=platform,
            account_scope=account_scope,
            conversation_id=conversation_id,
        )
        return bool(
            session
            and session.state in {SessionState.ACTIVE, SessionState.IDLE}
            and session.floor_owner_ref == actor_ref
        )

    def accept_turn(
        self,
        *,
        platform: str,
        account_scope: str,
        conversation_id: str,
        source_message_id: str,
        source_native_message_id: str,
        actor_ref: str,
        speaker_entity_id: str | None,
        direct: bool = False,
    ) -> TurnSnapshot:
        session = self.engage(
            platform=platform,
            account_scope=account_scope,
            conversation_id=conversation_id,
            actor_ref=actor_ref,
            speaker_entity_id=speaker_entity_id,
            direct=direct,
        )
        now = self.clock.now()
        turn_id = str(uuid4())

        with self.database._lock, self.database._conn:
            self.database._conn.execute(
                """INSERT INTO conversation_runtime_turns(
                       turn_id, session_id, source_message_id,
                       source_native_message_id, speaker_actor_ref,
                       speaker_entity_id, state, revision, created_at,
                       updated_at, reply_valid_until, cancellation_reason
                   ) VALUES (?, ?, ?, ?, ?, ?, 'accepted', 1, ?, ?, NULL, NULL)
                   ON CONFLICT(session_id, source_message_id) DO NOTHING""",
                (
                    turn_id,
                    session.session_id,
                    source_message_id,
                    source_native_message_id,
                    actor_ref,
                    speaker_entity_id,
                    now.isoformat(),
                    now.isoformat(),
                ),
            )
            row = self.database._conn.execute(
                """SELECT * FROM conversation_runtime_turns
                   WHERE session_id=? AND source_message_id=?""",
                (session.session_id, source_message_id),
            ).fetchone()
        turn = self._turn(row)
        assert turn is not None
        return turn

    def get_turn(self, turn_id: str) -> TurnSnapshot | None:
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT * FROM conversation_runtime_turns WHERE turn_id=?",
                (turn_id,),
            ).fetchone()
        return self._turn(row)

    def transition_turn(
        self,
        turn_id: str,
        *,
        expected: RuntimeTurnState,
        target: RuntimeTurnState,
        cancellation_reason: str | None = None,
    ) -> bool:
        now = self.clock.now()
        reply_valid_until = (
            now + timedelta(seconds=self.DELIVERY_SECONDS)
            if target == RuntimeTurnState.READY
            else None
        )

        with self.database._lock, self.database._conn:
            if target == RuntimeTurnState.READY:
                cursor = self.database._conn.execute(
                    """UPDATE conversation_runtime_turns
                       SET state=?, revision=revision+1, updated_at=?,
                           reply_valid_until=?, cancellation_reason=NULL
                       WHERE turn_id=? AND state=?""",
                    (
                        target.value,
                        now.isoformat(),
                        reply_valid_until.isoformat(),
                        turn_id,
                        expected.value,
                    ),
                )
            elif target in {
                RuntimeTurnState.CANCELLED,
                RuntimeTurnState.SUPERSEDED,
                RuntimeTurnState.FAILED,
            }:
                cursor = self.database._conn.execute(
                    """UPDATE conversation_runtime_turns
                       SET state=?, revision=revision+1, updated_at=?,
                           cancellation_reason=?
                       WHERE turn_id=? AND state=?""",
                    (
                        target.value,
                        now.isoformat(),
                        cancellation_reason,
                        turn_id,
                        expected.value,
                    ),
                )
            else:
                cursor = self.database._conn.execute(
                    """UPDATE conversation_runtime_turns
                       SET state=?, revision=revision+1, updated_at=?
                       WHERE turn_id=? AND state=?""",
                    (target.value, now.isoformat(), turn_id, expected.value),
                )
            return cursor.rowcount == 1

    def cancel_source(
        self,
        *,
        platform: str,
        account_scope: str,
        conversation_id: str,
        source_native_message_id: str,
        reason: str,
    ) -> int:
        session = self.get(
            platform=platform,
            account_scope=account_scope,
            conversation_id=conversation_id,
        )
        if session is None:
            return 0
        now = self.clock.now().isoformat()
        final_values = tuple(state.value for state in _FINAL_TURN_STATES)
        placeholders = ",".join("?" for _ in final_values)
        with self.database._lock, self.database._conn:
            cursor = self.database._conn.execute(
                f"""UPDATE conversation_runtime_turns
                    SET state='cancelled', revision=revision+1, updated_at=?,
                        cancellation_reason=?
                    WHERE session_id=? AND source_native_message_id=?
                      AND state NOT IN ({placeholders})""",
                (
                    now,
                    reason,
                    session.session_id,
                    source_native_message_id,
                    *final_values,
                ),
            )
            return cursor.rowcount

    def turn_deliverable(self, turn_id: str) -> bool:
        turn = self.get_turn(turn_id)
        if turn is None or turn.state in _FINAL_TURN_STATES:
            return False
        if turn.state in {RuntimeTurnState.ACCEPTED, RuntimeTurnState.THINKING}:
            return True
        if turn.reply_valid_until is None:
            return True
        return self.clock.now() <= turn.reply_valid_until
