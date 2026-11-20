"""Static PostgreSQL serving SQL coverage for Run 3 (no external database).

Only literal SQL executed by selected serving store methods is compared to the
reviewed SQL registry. Nonliteral queries must be explicitly declared as
overridden/guarded; this does NOT certify dynamic SQL or domain semantics.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

from amora.storage.database import AmoraDatabase
from amora.memory.store import MemoryStore
from amora.world.store import WorldStore
from amora.conversation.store import ConversationStore
from amora.cognition.store import CognitiveStore
from amora.actions.store import ActionStore
from amora.autonomy.store import AutonomyStore
from amora.emotion.store import EmotionStore
from amora.avatar.store import AvatarStore
from amora.messaging.store import MessagingStore
from amora.messaging.reflection_queue import ReflectionQueue
from amora.agenda import service as agenda
from amora.messaging import temporal_initiative as temporal
from amora.storage.postgres_runtime_generation import (
    CLAIM, RELEASE, STAGE, PREPARED, INVALIDATE,
)
from amora.storage.postgres_runtime_memory import (
    READY, COUNT, GET, PUT, CREATE, BASELINE, FTS_RANK,
    GENERATION_LEASE, REFLECTION_LEASE, AUTHORIZE_REFLECTION,
    OWNER_RECALL, SHAREABLE_OWNER,
)
from amora.storage.postgres_runtime_registry import compiled_domain_registry
from amora.storage.postgres_runtime_connection import _infra_sql


def _literal(expr: ast.AST) -> str | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return expr.value
    if isinstance(expr, ast.BinOp) and isinstance(expr.op, ast.Add):
        left, right = _literal(expr.left), _literal(expr.right)
        return left + right if left is not None and right is not None else None
    return None


def _sql_calls(obj, *, skip_methods: frozenset[str] = frozenset()):
    node = ast.parse(textwrap.dedent(inspect.getsource(obj)))
    statements = set()
    unresolved = []
    # Exclude overridden canonical methods; the PostgreSQL subclasses own
    # their read/writes. Never count SQLite-only paths as PG coverage.
    if isinstance(node.body[0], ast.ClassDef):
        nodes = [
            item for method in node.body[0].body
            if isinstance(method, (ast.FunctionDef, ast.AsyncFunctionDef))
            and method.name not in skip_methods
            for item in ast.walk(method)
        ]
    else:
        nodes = list(ast.walk(node))
    for item in nodes:
        if not isinstance(item, ast.Call):
            continue
        name = getattr(item.func, "attr", getattr(item.func, "id", ""))
        if name not in {"execute", "executemany", "execute_atomic_statement", "_write"}:
            continue
        ix = 2 if name == "execute_atomic_statement" else 0
        if len(item.args) <= ix:
            continue
        source = _literal(item.args[ix])
        if source is not None:
            statements.add(" ".join(source.split()))
        elif name != "_write":
            unresolved.append((name, item.lineno))
    return statements, unresolved


def test_original_serving_store_literal_sql_covered():
    approved = {**compiled_domain_registry(), **_infra_sql("amora_pg_v18_lab")}
    cases = [
        (AmoraDatabase, frozenset({"migrate", "integrity_check", "restore_backup"})),
        (ConversationStore, frozenset()),
        (CognitiveStore, frozenset({"list_messages"})),
        (WorldStore, frozenset({"query_claims", "list_self_entries",
                               "relationship_history", "_hydrate_claims"})),
        (ActionStore, frozenset()),
        (AutonomyStore, frozenset({"put_drives"})),
        (EmotionStore, frozenset({"list_events", "list_bids"})),
        (AvatarStore, frozenset({"_snapshot_asset_infos"})),
        (MessagingStore, frozenset({"list_audit", "put_outbound",
                                   "disclosure_is_current"})),
        (ReflectionQueue, frozenset()),
        (agenda, frozenset()),
        (temporal, frozenset()),
        # Canonical MemoryStore is replaced by PostgresMemoryStore for all
        # methods with SQL; the shared rank/RRF/MMR path is SQL-free.
    ]
    missing: dict[str,list[str]] = {}
    for cls, omitted in cases:
        sql, _dynamic = _sql_calls(cls, skip_methods=omitted)
        residual = sorted(sql - approved.keys())
        if residual:
            missing[cls.__name__] = residual
    assert not missing, {
        cls: [entry[:128] for entry in queries]
        for cls, queries in missing.items()
    }


def test_postgres_named_query_families_are_registered():
    approved = compiled_domain_registry()
    expected = {
        CLAIM, RELEASE, STAGE, PREPARED, INVALIDATE,
        READY, COUNT, GET, PUT, CREATE, BASELINE, FTS_RANK,
        GENERATION_LEASE, REFLECTION_LEASE, AUTHORIZE_REFLECTION,
        OWNER_RECALL, SHAREABLE_OWNER,
    }
    assert expected <= approved.keys()


def test_critical_sql_is_not_silently_translated_to_sqlite_dialect():
    approved = compiled_domain_registry()
    for query in approved.values():
        sql = query.postgres_sql
        assert "PRAGMA " not in sql
        assert "julianday(" not in sql
        assert "json_extract(" not in sql
        assert "sqlite_schema" not in sql
        assert "CREATE VIRTUAL TABLE" not in sql
