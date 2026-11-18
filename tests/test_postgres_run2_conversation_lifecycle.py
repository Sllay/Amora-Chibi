"""Run 2: full conversation runtime session and turn transitions on PG18."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import datetime,timezone
import os

import pytest

from amora.conversation.runtime import (
    AttentionState,ConversationRuntime,RuntimeTurnState,SessionState,
)
from amora.storage.database import AmoraDatabase
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",
    reason="No actual platform calls; disposable PG18 service only",
)
NOW=datetime(2026,11,17,tzinfo=timezone.utc)

def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_pg_conversation_session_turn_cas_source_edit_reopen(tmp_path):
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend=synthetic_core_backend(_raw,max_connections=3)
            clock=SimulatedClock(NOW)
            with closing(AmoraDatabase(
                tmp_path/"synthetic.db",backend=backend,
            )) as db,closing(AmoraDatabase(
                tmp_path/"synthetic.db",backend=backend,
            )) as another:
                db.migrate()
                runtime=ConversationRuntime(database=db,clock=clock)
                second=ConversationRuntime(database=another,clock=clock)
                fields=dict(
                    platform="discord",account_scope="primary",
                    conversation_id="fictional-DM",
                )
                engaged=runtime.engage(
                    **fields,actor_ref="synthetic-actor",
                    speaker_entity_id="PERSON_BK",direct=True,
                )
                assert engaged.state==SessionState.ACTIVE
                assert engaged.attention==AttentionState.DIRECT
                assert second.get(**fields).session_id==engaged.session_id
                first=runtime.accept_turn(
                    **fields,source_message_id="synthetic-rev-1",
                    source_native_message_id="synthetic-native",
                    actor_ref="synthetic-actor",
                    speaker_entity_id="PERSON_BK",direct=True,
                )
                assert first.state==RuntimeTurnState.ACCEPTED
                duplicate=second.accept_turn(
                    **fields,source_message_id="synthetic-rev-1",
                    source_native_message_id="synthetic-native",
                    actor_ref="synthetic-actor",
                    speaker_entity_id="PERSON_BK",direct=True,
                )
                assert duplicate.turn_id==first.turn_id
                assert runtime.transition_turn(
                    first.turn_id,expected=RuntimeTurnState.ACCEPTED,
                    target=RuntimeTurnState.THINKING,
                ) is True
                assert second.transition_turn(
                    first.turn_id,expected=RuntimeTurnState.ACCEPTED,
                    target=RuntimeTurnState.READY,
                ) is False
                assert runtime.turn_deliverable(first.turn_id) is True
                assert second.cancel_source(
                    **fields,source_native_message_id="synthetic-native",
                    reason="SOURCE_EDITED",
                )==1
                assert runtime.get_turn(first.turn_id).state==RuntimeTurnState.CANCELLED
                assert runtime.turn_deliverable(first.turn_id) is False
                assert runtime.transition_turn(
                    first.turn_id,expected=RuntimeTurnState.THINKING,
                    target=RuntimeTurnState.READY,
                ) is False
                second_rev=runtime.accept_turn(
                    **fields,source_message_id="synthetic-rev-2",
                    source_native_message_id="synthetic-native-new",
                    actor_ref="synthetic-actor",
                    speaker_entity_id="PERSON_BK",direct=True,
                )
                assert second_rev.turn_id!=first.turn_id
                assert runtime.transition_turn(
                    second_rev.turn_id,expected=RuntimeTurnState.ACCEPTED,
                    target=RuntimeTurnState.READY,
                ) is True
                assert runtime.transition_turn(
                    second_rev.turn_id,expected=RuntimeTurnState.READY,
                    target=RuntimeTurnState.DELIVERED,
                ) is True
                assert second.cancel_source(
                    **fields,source_native_message_id="synthetic-native-new",
                    reason="TOO_LATE",
                )==0
                closed=runtime.close(**fields)
                assert closed.state==SessionState.CLOSED
                reopened=second.engage(
                    **fields,actor_ref="synthetic-actor",
                    speaker_entity_id="PERSON_BK",direct=True,
                )
                assert reopened.session_id!=engaged.session_id

                # Two independent PostgreSQL connections racing to establish
                # a *new* transport session must not hit a duplicate PK.
                race_fields={**fields,"conversation_id":"fictional-race"}
                with ThreadPoolExecutor(max_workers=2) as pool:
                    left=pool.submit(
                        runtime.engage,**race_fields,
                        actor_ref="fictional-first",
                        speaker_entity_id="PERSON_BK",direct=True,
                    )
                    right=pool.submit(
                        second.engage,**race_fields,
                        actor_ref="fictional-second",
                        speaker_entity_id="PERSON_BK",direct=True,
                    )
                    a,b=left.result(),right.result()
                assert a.session_id==b.session_id
                assert second.get(**race_fields).state==SessionState.ACTIVE
                assert manager.execute("""
                    SELECT COUNT(*) FROM amora_pg_v18_lab.conversation_runtime_sessions
                    WHERE conversation_id='fictional-race'
                """).fetchone()[0]==1
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
