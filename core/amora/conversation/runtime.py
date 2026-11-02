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


class TurnState(StrEnum):
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


@dataclass(frozen=True, slots=True)
class SessionSnapshot:
    session_id: str
    platform: str
    account_scope: str
    conversation_id: str
    state: SessionState
    attention: AttentionState
    revision: int
    floor_actor_ref: str | None
    floor_entity_id: str | None
    last_activity_at: datetime
    idle_since: datetime | None


@dataclass(frozen=True, slots=True)
class TurnSnapshot:
    turn_id: str
    session_id: str
    source_message_id: str
    actor_ref: str
    speaker_entity_id: str | None
    state: TurnState
    revision: int
    received_at: datetime
    lease_until: datetime


class ConversationRuntime:
    """Transport-neutral persistent conversation state machine.

    Session lifetime, floor ownership and delivery lifetime are deliberately
    separate. A newer message may advance the session without invalidating an
    already accepted turn. This is the authority that messaging/voice adapters
    should converge on; platform-specific routers remain policy adapters.
    """

    ACTIVE_SECONDS = 180
    DORMANT_SECONDS = 900
    TURN_LEASE_SECONDS = 120

    def __init__(self, *, database, clock: Clock) -> None:
        self.database = database
        self.clock = clock

    @staticmethod
    def _key(platform: str, account_scope: str, conversation_id: str) -> tuple[str, str, str]:
        return platform, account_scope, conversation_id

    def _row(self, key):
        return self.database._conn.execute(
            "SELECT * FROM conversation_runtime_sessions WHERE platform=? AND account_scope=? AND conversation_id=?",
            key,
        ).fetchone()

    @staticmethod
    def _session(row) -> SessionSnapshot | None:
        if row is None:
            return None
        return SessionSnapshot(
            session_id=str(row["session_id"]), platform=str(row["platform"]),
            account_scope=str(row["account_scope"]), conversation_id=str(row["conversation_id"]),
            state=SessionState(row["state"]), attention=AttentionState(row["attention_state"]),
            revision=int(row["revision"]), floor_actor_ref=row["floor_actor_ref"],
            floor_entity_id=row["floor_entity_id"], last_activity_at=datetime.fromisoformat(row["last_activity_at"]),
            idle_since=datetime.fromisoformat(row["idle_since"]) if row["idle_since"] else None,
        )

    def get(self, *, platform: str, account_scope: str, conversation_id: str) -> SessionSnapshot | None:
        key = self._key(platform, account_scope, conversation_id)
        with self.database._lock:
            return self._session(self._row(key))

    def engage(self, *, platform: str, account_scope: str, conversation_id: str,
               actor_ref: str, speaker_entity_id: str | None, direct: bool) -> SessionSnapshot:
        key = self._key(platform, account_scope, conversation_id)
        now = self.clock.now()
        with self.database._lock, self.database._conn:
            row = self._row(key)
            session_id = str(row["session_id"]) if row is not None else str(uuid4())
            attention = AttentionState.DIRECT if direct else AttentionState.ENGAGED
            self.database._conn.execute(
                """INSERT INTO conversation_runtime_sessions(
                       platform, account_scope, conversation_id, session_id, state, attention_state,
                       revision, floor_actor_ref, floor_entity_id, opened_at, last_activity_at, idle_since)
                   VALUES (?, ?, ?, ?, 'active', ?, 1, ?, ?, ?, ?, NULL)
                   ON CONFLICT(platform, account_scope, conversation_id) DO UPDATE SET
                       state='active', attention_state=excluded.attention_state,
                       revision=conversation_runtime_sessions.revision + 1,
                       floor_actor_ref=excluded.floor_actor_ref, floor_entity_id=excluded.floor_entity_id,
                       last_activity_at=excluded.last_activity_at, idle_since=NULL""",
                (*key, session_id, attention.value, actor_ref, speaker_entity_id, now.isoformat(), now.isoformat()),
            )
            return self._session(self._row(key))

    def tick(self, *, platform: str, account_scope: str, conversation_id: str) -> SessionSnapshot | None:
        key = self._key(platform, account_scope, conversation_id)
        now = self.clock.now()
        with self.database._lock, self.database._conn:
            row = self._row(key)
            session = self._session(row)
            if session is None or session.state == SessionState.CLOSED:
                return session
            age = (now - session.last_activity_at).total_seconds()
            if age >= self.DORMANT_SECONDS and session.state != SessionState.DORMANT:
                self.database._conn.execute(
                    "UPDATE conversation_runtime_sessions SET state='dormant', attention_state='disengaged', revision=revision+1, floor_actor_ref=NULL, floor_entity_id=NULL WHERE platform=? AND account_scope=? AND conversation_id=?",
                    key,
                )
            elif age >= self.ACTIVE_SECONDS and session.state == SessionState.ACTIVE:
                self.database._conn.execute(
                    "UPDATE conversation_runtime_sessions SET state='idle', attention_state='monitoring', revision=revision+1, idle_since=? WHERE platform=? AND account_scope=? AND conversation_id=?",
                    (now.isoformat(), *key),
                )
            return self._session(self._row(key))

    def accepts_continuity(self, *, platform: str, account_scope: str, conversation_id: str, actor_ref: str) -> bool:
        session = self.tick(platform=platform, account_scope=account_scope, conversation_id=conversation_id)
        return bool(session and session.state in {SessionState.ACTIVE, SessionState.IDLE} and session.floor_actor_ref == actor_ref)

    def accept_turn(self, *, platform: str, account_scope: str, conversation_id: str,
                    source_message_id: str, actor_ref: str, speaker_entity_id: str | None) -> TurnSnapshot:
        session = self.engage(platform=platform, account_scope=account_scope, conversation_id=conversation_id,
                              actor_ref=actor_ref, speaker_entity_id=speaker_entity_id, direct=False)
        now = self.clock.now(); lease = now + timedelta(seconds=self.TURN_LEASE_SECONDS); turn_id = str(uuid4())
        with self.database._lock, self.database._conn:
            self.database._conn.execute(
                """INSERT INTO conversation_runtime_turns(
                       turn_id, session_id, source_message_id, actor_ref, speaker_entity_id,
                       state, revision, received_at, updated_at, lease_until)
                   VALUES (?, ?, ?, ?, ?, 'accepted', 1, ?, ?, ?)""",
                (turn_id, session.session_id, source_message_id, actor_ref, speaker_entity_id,
                 now.isoformat(), now.isoformat(), lease.isoformat()),
            )
        return TurnSnapshot(turn_id, session.session_id, source_message_id, actor_ref, speaker_entity_id,
                            TurnState.ACCEPTED, 1, now, lease)

    def transition_turn(self, turn_id: str, *, expected: TurnState, target: TurnState) -> bool:
        now = self.clock.now().isoformat()
        with self.database._lock, self.database._conn:
            cursor = self.database._conn.execute(
                "UPDATE conversation_runtime_turns SET state=?, revision=revision+1, updated_at=? WHERE turn_id=? AND state=?",
                (target.value, now, turn_id, expected.value),
            )
            return cursor.rowcount == 1

    def turn_deliverable(self, turn_id: str) -> bool:
        with self.database._lock:
            row = self.database._conn.execute(
                "SELECT state, lease_until FROM conversation_runtime_turns WHERE turn_id=?", (turn_id,)
            ).fetchone()
        if row is None or TurnState(row["state"]) in {TurnState.CANCELLED, TurnState.SUPERSEDED, TurnState.DELIVERED}:
            return False
        return self.clock.now() <= datetime.fromisoformat(row["lease_until"])
