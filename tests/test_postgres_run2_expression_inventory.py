"""Guard reviewed SQL completeness for expression hydration in PostgreSQL Core.

A native Discord message first enters ExpressionMemory even without stickers.
Missing reviewed SQL here causes a runtime reject before identity/attention.
"""
from amora.messaging.expression_memory import ExpressionMemory
from amora.storage.postgres_runtime_registry import compiled_domain_registry
from test_postgres_run3_sql_inventory import _sql_calls


def test_all_expression_literal_sql_is_reviewed_for_postgres():
    sources, unresolved=_sql_calls(ExpressionMemory)
    registry=compiled_domain_registry()
    missing=sources-registry.keys()
    assert not missing, sorted(missing)
    assert not unresolved, unresolved
    assert len(sources)>=19


def test_expression_postgres_sql_is_schema_qualified_and_parameterized():
    registry=compiled_domain_registry()
    reviewed=[
        (src,item) for src,item in registry.items()
        if "perceptual_expression_" in src
    ]
    assert len(reviewed)>=19
    for src,item in reviewed:
        assert "amora_pg_v18_lab.perceptual_expression_" in item.postgres_sql
        assert item.parameter_count==item.postgres_sql.count("%s")
        assert "json_extract(" not in item.postgres_sql
