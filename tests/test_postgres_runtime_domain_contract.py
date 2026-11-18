"""Run 2 SQL mapping and fail-closed domain assembly contracts.

Static contracts, not evidence of full Core runtime or real Aiven readiness.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

from amora.conversation.store import ConversationStore
from amora.cognition.store import CognitiveStore
from amora.storage.postgres_runtime_conversation import conversation_statements
from amora.storage.postgres_runtime_identity import identity_statements
from amora.storage.postgres_runtime_memory import (
    memory_statements, GENERATION_LEASE, AUTHORIZE_REFLECTION,
    SHAREABLE_OWNER, FTS_RANK, PUT, CREATE,
)
from amora.storage.postgres_runtime_voice import (
    voice_stop_statements,VOICE_LOCK_TURN,
)
from amora.storage.postgres_runtime_cognition import cognition_statements,HISTORY
from amora.storage.postgres_runtime_world import world_statements
from amora.storage.postgres_runtime_actions import action_statements
from amora.storage.postgres_runtime_avatar import avatar_statements
from amora.storage.postgres_runtime_autonomy import autonomy_statements
from amora.storage.postgres_runtime_emotion import emotion_statements
from amora.storage.postgres_runtime_messaging import messaging_statements,OUTBOUND_INSERT
from amora.storage.postgres_runtime_generation import generation_statements
from amora.storage.postgres_runtime_reflections import reflection_statements
from amora.storage.postgres_runtime_agenda import agenda_statements
from amora.storage.postgres_runtime_temporal import temporal_statements
from amora.storage.postgres_runtime_episode import episode_statements,EPISODE_FENCE_JOB
from amora.storage.postgres_runtime_expression import expression_statements
from amora.storage.postgres_runtime_registry import compiled_domain_registry
from amora.storage.postgres_runtime_connection import (
    PostgresBackendCandidate,PostgresRuntimeError,PostgresTargetPolicy,
)


def _canonical_sql_literals(store):
    tree=ast.parse(textwrap.dedent(inspect.getsource(store)))
    found=set()
    for node in ast.walk(tree):
        if not isinstance(node,ast.Call):
            continue
        fn=getattr(node.func,"attr",getattr(node.func,"id",""))
        if fn not in {"execute","executemany","execute_atomic_statement"}:
            continue
        position=2 if fn=="execute_atomic_statement" else 0
        if len(node.args)<=position:
            continue
        sql=node.args[position]
        if isinstance(sql,ast.Constant) and isinstance(sql.value,str):
            found.add(" ".join(sql.value.split()))
    return found


def test_original_conversation_statements_are_all_reviewed():
    static=_canonical_sql_literals(ConversationStore)
    approved=conversation_statements()
    assert len(static)>=14
    assert static<=set(approved)
    assert all("amora_pg_v18_lab." in item.postgres_sql
               for item in approved.values())
    assert all("?" not in item.postgres_sql for item in approved.values())


def test_cognition_static_sql_and_dynamic_history_have_explicit_coverage():
    reviewed=cognition_statements()
    assert HISTORY in reviewed
    assert "jsonb" in reviewed[HISTORY].postgres_sql.lower()
    assert "memory_reflection_invalidations" in reviewed[HISTORY].postgres_sql
    # Dynamic f-string list_messages is replaced by PostgresCognitiveStore.
    assert len(reviewed)>=15


def test_memory_privacy_and_index_paths_keep_sql_boundaries():
    reviewed=memory_statements()
    for name in (GENERATION_LEASE,AUTHORIZE_REFLECTION,
                 SHAREABLE_OWNER,FTS_RANK,PUT,CREATE):
        assert name in reviewed
        assert "amora_pg_v18_lab." in reviewed[name].postgres_sql
    assert "jsonb_array_elements_text" in reviewed[SHAREABLE_OWNER].postgres_sql
    assert "privacy_scope" in reviewed[FTS_RANK].postgres_sql
    assert "ON CONFLICT" in reviewed[CREATE].postgres_sql


def test_cross_domain_registry_has_no_silent_overrides():
    registry=compiled_domain_registry()
    domains=(
        identity_statements(),conversation_statements(),memory_statements(),
        voice_stop_statements(),cognition_statements(),world_statements(),
        action_statements(),avatar_statements(),autonomy_statements(),
        emotion_statements(),messaging_statements(),
        generation_statements(),reflection_statements(),agenda_statements(),
        temporal_statements(),episode_statements(),expression_statements(),
    )
    assert len(registry)==sum(len(domain) for domain in domains)
    assert VOICE_LOCK_TURN in registry
    assert EPISODE_FENCE_JOB in registry
    assert "SELECT * FROM conversation_messages WHERE dedupe_key = ?" in registry
    assert "SELECT * FROM devices WHERE id = ?" in registry


def test_factory_stays_synthetic_and_production_selector_unchanged():
    from amora.storage.postgres_runtime_registry import synthetic_core_backend
    backend=synthetic_core_backend(lambda: None)
    assert isinstance(backend,PostgresBackendCandidate)
    assert backend.capabilities.name=="postgres-candidate"
    assert backend.opened_connections==0
    assert backend._target.host=="127.0.0.1"
    assert backend._target.database=="amora_pg_synthetic"
    assert backend._target.schema=="amora_pg_v18_lab"


def test_messaging_outbound_sql_is_fully_parameterized_and_guarded():
    statement=messaging_statements()[OUTBOUND_INSERT]
    assert statement.parameter_count==23
    assert statement.postgres_sql.count("%s")==23
    assert "messaging_inbound_generation" in statement.postgres_sql
    assert "memory_reflection_invalidations" in statement.postgres_sql
    assert "memory_records" in statement.postgres_sql
    assert "ON CONFLICT" in statement.postgres_sql


def test_validation_run1_import_and_bind_count():
    """Exercise every registered statement at import time without external DB."""
    import importlib
    from amora.storage.postgres_runtime_registry import compiled_domain_registry
    candidates = (
        "amora.memory.postgres_store",
        "amora.cognition.postgres_store",
        "amora.conversation.postgres_store",
        "amora.world.postgres_store",
        "amora.emotion.postgres_store",
        "amora.autonomy.postgres_store",
        "amora.messaging.postgres_store",
        "amora.memory.postgres_episode_fence",
    )
    for module in candidates:
        assert importlib.import_module(module) is not None
    statements = compiled_domain_registry()
    assert len(statements) > 100
    bad = {
        key: (sql.parameter_count, sql.postgres_sql.count("%s"))
        for key, sql in statements.items()
        if sql.parameter_count != sql.postgres_sql.count("%s")
    }
    assert bad == {}, bad


def test_postgres_memory_text_rank_preserves_original_lexical_fallback():
    from datetime import datetime, timezone
    from threading import RLock
    from types import SimpleNamespace
    from amora.memory.postgres_store import PostgresMemoryStore
    from amora.storage import postgres_runtime_memory as memory_sql

    class QueryResult:
        def fetchall(self):
            return []

    class EmptyBM25:
        def execute(self, query, params=()):
            assert query == memory_sql.FTS_RANK
            return QueryResult()

    memory = object.__new__(PostgresMemoryStore)
    memory._lock = RLock()
    memory._conn = EmptyBM25()
    memory.clock = SimpleNamespace(now=lambda: datetime(
        2026, 1, 1, tzinfo=timezone.utc,
    ))
    records = {
        "record-one": SimpleNamespace(
            content="A cenoura e laranja", summary=None, tags=set(),
        ),
        "record-two": SimpleNamespace(
            content="A floresta cresce", summary=None, tags=set(),
        ),
    }
    assert memory._text_rank("cenoura", records) == ["record-one"]
    assert memory._text_rank("sem correspondencia", records) == []
