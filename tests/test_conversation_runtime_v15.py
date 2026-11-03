from __future__ import annotations

from datetime import timedelta

from amora.conversation.runtime import ConversationRuntime, RuntimeTurnState, SessionState
from amora.storage import AmoraDatabase, LATEST_SCHEMA_VERSION
from amora.time import SimulatedClock


def test_runtime_v15_schema_and_turn_lifecycle(tmp_path) -> None:
    database = AmoraDatabase(tmp_path / "amora.db")
    clock = SimulatedClock()
    try:
        assert database.migrate() == LATEST_SCHEMA_VERSION
        assert database.schema_version() == LATEST_SCHEMA_VERSION

        session_columns = {
            row["name"] for row in database._conn.execute("PRAGMA table_info(conversation_runtime_sessions)")
        }
        assert {"floor_owner_ref", "floor_owner_entity_id", "idle_at", "revision"} <= session_columns

        turn_columns = {
            row["name"] for row in database._conn.execute("PRAGMA table_info(conversation_runtime_turns)")
        }
        assert {
            "source_native_message_id",
            "speaker_actor_ref",
            "revision",
            "created_at",
            "reply_valid_until",
        } <= turn_columns

        runtime = ConversationRuntime(database=database, clock=clock)
        opened = runtime.engage(
            platform="discord",
            account_scope="primary",
            conversation_id="channel-1",
            actor_ref="EXT_DISCORD_BK",
            speaker_entity_id="PERSON_BK",
            direct=True,
        )
        assert opened.state == SessionState.ACTIVE
        assert opened.floor_owner_ref == "EXT_DISCORD_BK"

        clock.advance(timedelta(seconds=181))
        idle = runtime.tick(
            platform="discord", account_scope="primary", conversation_id="channel-1"
        )
        assert idle is not None and idle.state == SessionState.IDLE
        assert runtime.accepts_continuity(
            platform="discord",
            account_scope="primary",
            conversation_id="channel-1",
            actor_ref="EXT_DISCORD_BK",
        )

        turn = runtime.accept_turn(
            platform="discord",
            account_scope="primary",
            conversation_id="channel-1",
            source_message_id="message-1",
            source_native_message_id="native-1",
            actor_ref="EXT_DISCORD_BK",
            speaker_entity_id="PERSON_BK",
        )
        duplicate = runtime.accept_turn(
            platform="discord",
            account_scope="primary",
            conversation_id="channel-1",
            source_message_id="message-1",
            source_native_message_id="native-1",
            actor_ref="EXT_DISCORD_BK",
            speaker_entity_id="PERSON_BK",
        )
        assert duplicate.turn_id == turn.turn_id
        assert turn.reply_valid_until is None

        assert runtime.transition_turn(
            turn.turn_id,
            expected=RuntimeTurnState.ACCEPTED,
            target=RuntimeTurnState.THINKING,
        )
        clock.advance(timedelta(minutes=10))
        assert runtime.turn_deliverable(turn.turn_id)

        assert runtime.transition_turn(
            turn.turn_id,
            expected=RuntimeTurnState.THINKING,
            target=RuntimeTurnState.READY,
        )
        ready = runtime.get_turn(turn.turn_id)
        assert ready is not None and ready.reply_valid_until is not None
        assert runtime.turn_deliverable(turn.turn_id)
        clock.advance(timedelta(seconds=121))
        assert not runtime.turn_deliverable(turn.turn_id)

        second = runtime.accept_turn(
            platform="discord",
            account_scope="primary",
            conversation_id="channel-1",
            source_message_id="message-2",
            source_native_message_id="native-2",
            actor_ref="EXT_DISCORD_BK",
            speaker_entity_id="PERSON_BK",
        )
        assert runtime.cancel_source(
            platform="discord",
            account_scope="primary",
            conversation_id="channel-1",
            source_native_message_id="native-2",
            reason="message_deleted",
        ) == 1
        cancelled = runtime.get_turn(second.turn_id)
        assert cancelled is not None and cancelled.state == RuntimeTurnState.CANCELLED
        assert runtime.get_turn(turn.turn_id).state == RuntimeTurnState.READY
    finally:
        database.close()


def test_closed_session_reopens_with_new_session_id(tmp_path) -> None:
    database = AmoraDatabase(tmp_path / "amora.db")
    clock = SimulatedClock()
    try:
        database.migrate()
        runtime = ConversationRuntime(database=database, clock=clock)
        first = runtime.engage(
            platform="discord",
            account_scope="primary",
            conversation_id="channel-2",
            actor_ref="EXT_DISCORD_BK",
            speaker_entity_id="PERSON_BK",
            direct=True,
        )
        closed = runtime.close(
            platform="discord", account_scope="primary", conversation_id="channel-2"
        )
        assert closed is not None and closed.state == SessionState.CLOSED

        reopened = runtime.engage(
            platform="discord",
            account_scope="primary",
            conversation_id="channel-2",
            actor_ref="EXT_DISCORD_BK",
            speaker_entity_id="PERSON_BK",
            direct=True,
        )
        assert reopened.state == SessionState.ACTIVE
        assert reopened.session_id != first.session_id
    finally:
        database.close()
