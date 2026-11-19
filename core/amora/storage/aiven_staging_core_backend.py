"""Explicit, fail-closed Aiven staging Core backend.

The real Core can opt in ONLY with postgres-staging plus an independent
literal approval. No automatic production routing, no schema creation, no
SQLite/libSQL fallback, no migration, no credential printing. The external
operational backup/rollback and release gates are separate and mandatory.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

from .aiven_preflight import load_aiven_ca_certificate, validate_aiven_settings
from .postgres_runtime_connection import (
    PostgresBackendCandidate, PostgresRuntimeError, PostgresTargetPolicy,
)
from .postgres_runtime_registry import compiled_domain_registry
from .postgres_v18_lab_schema import LAB_SCHEMA


_APPROVAL = "ALLOW_ONLY_ISOLATED_AIVEN_STAGING_CORE"
_MAX_CORE_CONNECTIONS = 16


def staging_core_backend_from_environment() -> PostgresBackendCandidate:
    """Build a staging-only backend, requiring target pinning and mounted CA.

    Does not connect until Core requests its first connection. Never creates
    tables or upgrades the source and never auto-selects from production.
    """
    if os.getenv("AMORA_PG_STAGING_APPROVAL", "") != _APPROVAL:
        raise PostgresRuntimeError("PG_STAGING_OPERATOR_APPROVAL_REQUIRED")

    dsn = os.getenv("AMORA_POSTGRES_DSN", "")
    ca_file = os.getenv("AMORA_POSTGRES_CA_FILE", "")
    # A file mount is mandatory for a long-running Core process; unlike a
    # one-shot job, keep no plaintext PEM or generated certificate file.
    if not ca_file or os.getenv("AMORA_POSTGRES_CA_PEM"):
        raise PostgresRuntimeError("PG_STAGING_MOUNTED_CA_REQUIRED")

    try:
        ca = load_aiven_ca_certificate(file_path=ca_file)
        validate_aiven_settings(dsn, ca)
        endpoint = urlsplit(dsn)
        host, port, database = endpoint.hostname, endpoint.port, endpoint.path[1:]
        pinned_host = os.getenv("AMORA_STAGING_EXPECTED_HOST", "")
        pinned_port = int(os.getenv("AMORA_STAGING_EXPECTED_PORT", "0"))
        pinned_database = os.getenv("AMORA_STAGING_EXPECTED_DATABASE", "")
        if not pinned_host or not pinned_port or not pinned_database:
            raise PostgresRuntimeError("PG_STAGING_ENDPOINT_PIN_REQUIRED")
        if (host != pinned_host or port != pinned_port or database != pinned_database
                or not host or not port or not database or "%" in database
                or "/" in database or "\x00" in database):
            raise PostgresRuntimeError("PG_STAGING_ENDPOINT_PIN_MISMATCH")
        target = PostgresTargetPolicy(
            host=host, port=port, database=database,
            schema=LAB_SCHEMA, server_major=18, require_tls=True,
        )
    except PostgresRuntimeError:
        raise
    except Exception:
        raise PostgresRuntimeError("PG_STAGING_CONFIGURATION_INVALID") from None

    def connect():
        try:
            import psycopg
            connection = psycopg.connect(
                dsn, sslmode="verify-full", sslrootcert=ca_file,
                connect_timeout=8, autocommit=True,
                application_name="amora-staging-core-guarded",
                options="-c search_path=pg_catalog",
            )
        except Exception:
            raise PostgresRuntimeError("PG_STAGING_CONNECT_FAILED") from None
        try:
            target.verify(connection)
        except BaseException:
            connection.close()
            raise
        return connection

    return PostgresBackendCandidate(
        connect, compiled_domain_registry(), target=target,
        max_connections=_MAX_CORE_CONNECTIONS,
    )
