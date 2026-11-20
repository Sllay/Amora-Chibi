"""Review SQL reachable from a native Discord Core turn BEFORE the live PG run.

The SQLite source is not rewritten. Literal statements must be present as
individually audited PostgreSQL mappings; computed statements must remain
explicitly fenced by a PostgreSQL-specific path.
"""
from amora.messaging.attention import ConversationFloor, SocialAttentionRouter
from amora.conversation.runtime import ConversationRuntime
from amora.messaging.service import PlatformMessagingService
from amora.storage.postgres_runtime_registry import compiled_domain_registry
from test_postgres_run3_sql_inventory import _sql_calls


def test_discord_core_entrypoint_static_sql_inventory():
    registry=compiled_domain_registry()
    missing={}
    for owner in (
        ConversationFloor, SocialAttentionRouter,
        ConversationRuntime, PlatformMessagingService,
    ):
        sources, _nonliteral=_sql_calls(owner)
        absent=sorted(sources-registry.keys())
        if absent:
            missing[owner.__name__]=absent
    assert not missing, missing


def test_runtime_cancel_is_explicitly_pg_reviewed():
    from amora.storage.postgres_runtime_conversation_lifecycle import CANCEL_SOURCE
    stmt=compiled_domain_registry()[CANCEL_SOURCE]
    assert "amora_pg_v18_lab.conversation_runtime_turns" in stmt.postgres_sql
    assert ("NOT IN" in stmt.postgres_sql or "NOT (state=ANY(" in stmt.postgres_sql)
    assert stmt.parameter_count==stmt.postgres_sql.count("%s")
