"""Explicit opt-in assembly of original Core PostgreSQL domain mappings.

The reviewed mappings in this module are tied to a disposable PG18 v18
schema. No production Aiven/Turso connection, environment backend selector,
credential loading or automatic deployment occurs here.
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from .postgres_runtime_connection import (
    PostgresBackendCandidate, PostgresRuntimeError, PostgresTargetPolicy,
    ReviewedStatement,
)
from .postgres_runtime_conversation import conversation_statements
from .postgres_runtime_identity import identity_statements
from .postgres_runtime_memory import memory_statements
from .postgres_runtime_voice import voice_stop_statements
from .postgres_runtime_cognition import cognition_statements
from .postgres_runtime_world import world_statements


def compiled_domain_registry() -> dict[str, ReviewedStatement]:
    """Merge identity, event, conversation and voice SQL with no overrides."""
    registry: dict[str, ReviewedStatement] = {}
    for domain in (identity_statements(), conversation_statements(), memory_statements(), voice_stop_statements(), cognition_statements(), world_statements()):
        for key, value in domain.items():
            if key in registry:
                raise PostgresRuntimeError("PG_RUNTIME_DUPLICATE_DOMAIN_SQL")
            registry[key] = value
    return registry


def synthetic_core_backend(
    factory: Callable[[], Any], *, max_connections: int = 16,
) -> PostgresBackendCandidate:
    """Prepare one injected candidate for original Core stores in synthetic PG.

    This backend is not yet a complete Core bootstrap backend: other stores,
    especially MemoryStore and dynamic SQL paths, remain blocked until
    reviewed domain mappings are wired.
    """
    return PostgresBackendCandidate(
        factory,
        compiled_domain_registry(),
        target=PostgresTargetPolicy(
            host="127.0.0.1", port=5432,
            database="amora_pg_synthetic",
            schema="amora_pg_v18_lab",
        ),
        max_connections=max_connections,
    )
