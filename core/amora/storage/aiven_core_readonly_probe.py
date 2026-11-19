"""One-shot READ-ONLY real Aiven v18 Core memory/identity continuity probe.

Only aggregate flags and counts leave the process. The provider, Discord, Core
bootstrap and every write path are deliberately DISABLED. This is evidence
before a separate authorized write test, NOT production cutover permission.
"""
from __future__ import annotations

from contextlib import closing
from pathlib import Path
import json
import os
import re
from urllib.parse import urlsplit

from .aiven_preflight import load_aiven_ca_certificate, validate_aiven_settings
from .database import AmoraDatabase
from .postgres_run3_preflight import inspect_prepared_postgres
from .postgres_runtime_connection import (
    PostgresBackendCandidate, PostgresRuntimeError, PostgresTargetPolicy,
)
from .postgres_runtime_registry import compiled_domain_registry
from .postgres_v18_lab_schema import LAB_SCHEMA


class AivenReadOnlyProbeError(RuntimeError):
    pass


def _settings() -> tuple[str, str, PostgresTargetPolicy]:
    """Validate the exact TLS destination and mounted CA without echoing it."""
    dsn = os.getenv("AMORA_POSTGRES_DSN", "")
    ca_path = os.getenv("AMORA_POSTGRES_CA_FILE", "")
    try:
        if not ca_path or os.getenv("AMORA_POSTGRES_CA_PEM"):
            raise AivenReadOnlyProbeError("PG_READONLY_CA_FILE_REQUIRED")
        ca = load_aiven_ca_certificate(file_path=ca_path)
        validate_aiven_settings(dsn, ca)
        endpoint = urlsplit(dsn)
        pinned_host = os.getenv("AMORA_STAGING_EXPECTED_HOST", "")
        pinned_port = int(os.getenv("AMORA_STAGING_EXPECTED_PORT", "0"))
        pinned_database = os.getenv("AMORA_STAGING_EXPECTED_DATABASE", "")
        if (not pinned_host or not pinned_port or not pinned_database
                or endpoint.hostname != pinned_host
                or endpoint.port != pinned_port
                or endpoint.path[1:] != pinned_database
                or "%" in pinned_database or "/" in pinned_database):
            raise AivenReadOnlyProbeError("PG_READONLY_ENDPOINT_PIN_MISMATCH")
        policy = PostgresTargetPolicy(
            host=pinned_host, port=pinned_port, database=pinned_database,
            schema=LAB_SCHEMA, server_major=18, require_tls=True,
        )
        return dsn, ca_path, policy
    except AivenReadOnlyProbeError:
        raise
    except Exception:
        raise AivenReadOnlyProbeError("PG_READONLY_CONFIGURATION_BLOCKED") from None


def inspect(*, connection_factory=None) -> dict[str, object]:
    """Read historical memory through original Core stores under PG read-only."""
    dsn, ca_file, target = _settings()
    if connection_factory is None:
        try:
            import psycopg
        except ImportError:
            raise AivenReadOnlyProbeError("PG_READONLY_DRIVER_UNAVAILABLE") from None

        def connection_factory():
            try:
                raw = psycopg.connect(
                    dsn, sslmode="verify-full", sslrootcert=ca_file,
                    connect_timeout=8, autocommit=True,
                    application_name="amora-core-readonly-continuity-audit",
                    options="-c default_transaction_read_only=on -c search_path=pg_catalog",
                )
            except Exception:
                raise AivenReadOnlyProbeError("PG_READONLY_CONNECTION_FAILED") from None
            try:
                target.verify(raw)
                readonly = raw.execute(
                    "SELECT current_setting('default_transaction_read_only')"
                ).fetchone()[0]
                if readonly != "on":
                    raise AivenReadOnlyProbeError("PG_READONLY_NOT_ENFORCED")
            except BaseException:
                raw.close()
                raise
            return raw

    with closing(connection_factory()) as raw:
        target.verify(raw)
        report = inspect_prepared_postgres(
            raw, target, require_memory_epoch=True,
            allow_empty_migrations_for_import=False,
        )
        if report.status != "structural_pass":
            raise AivenReadOnlyProbeError("PG_READONLY_STRUCTURE_BLOCKED")

        backend = PostgresBackendCandidate(
            connection_factory, compiled_domain_registry(), target=target,
            max_connections=3,
        )
        # Local path is a protocol placeholder; no SQLite file is opened.
        db = AmoraDatabase(Path("/tmp/amora-pg-readonly-probe.db"), backend=backend)
        try:
            db.migrate()  # on PG this is a strict READ-ONLY schema check
            if db.schema_version() != 18:
                raise AivenReadOnlyProbeError("PG_READONLY_SCHEMA_VERSION_DRIFT")
            event_count = db.event_count()
            registry = db.load_registry()
            if registry is None or not registry.entities:
                raise AivenReadOnlyProbeError("PG_READONLY_IDENTITIES_MISSING")

            from amora.memory.models import MemoryQuery
            from amora.memory.postgres_store import PostgresMemoryStore
            from amora.time import SystemClock

            with closing(PostgresMemoryStore(
                Path("/tmp/amora-pg-readonly-probe.db"), SystemClock(), registry,
                backend=backend,
            )) as memories:
                memory_count = memories.count(include_archived=True)
                if memory_count < 1:
                    raise AivenReadOnlyProbeError("PG_READONLY_MEMORIES_MISSING")
                chosen = raw.execute(
                    "SELECT id FROM amora_pg_v18_lab.memory_records ORDER BY id LIMIT 1"
                ).fetchone()
                if not chosen or memories.get(chosen[0]) is None:
                    raise AivenReadOnlyProbeError("PG_READONLY_HISTORICAL_HYDRATION_FAILED")

                # A real private indexed record is tested without logging any
                # person, memory ID, text or token. No fabricated match is used.
                private = raw.execute("""
                    SELECT m.id,m.owner_entity_id,p.term
                    FROM amora_pg_v18_lab.memory_records m
                    JOIN amora_pg_v18_lab.fts_term_postings p
                      ON p.memory_id=m.id
                    WHERE m.owner_entity_id IS NOT NULL
                      AND m.owner_entity_id<>''
                      AND m.privacy_scope='PRIVATE'
                      AND m.archived_at IS NULL
                      AND m.valid_until IS NULL
                      AND LENGTH(p.term)>=4
                    ORDER BY m.id,p.term
                    LIMIT 1
                """).fetchone()
                privacy_result = "no_eligible_indexed_private_sample"
                if private is not None:
                    memory_id, owner, token = private
                    allowed = memories.rank(MemoryQuery(
                        text=token, projection_recipient_id=owner,
                        require_topic_match=True,
                    ))
                    if not any(str(hit.memory.id) == memory_id for hit in allowed):
                        raise AivenReadOnlyProbeError("PG_READONLY_OWNER_RECALL_FAILED")
                    denied = memories.rank(MemoryQuery(
                        text=token,
                        projection_recipient_id="AMORA_UNRELATED_PROBE_PRINCIPAL",
                        require_topic_match=True,
                    ))
                    if any(str(hit.memory.id) == memory_id for hit in denied):
                        raise AivenReadOnlyProbeError("PG_READONLY_PRIVATE_MEMORY_DISCLOSED")
                    privacy_result = "owner_recall_allowed_other_principal_denied"
                return {
                    "status": "real_staging_read_only_core_continuity_pass",
                    "schema_version": 18,
                    "relational_structure": report.status,
                    "events": event_count,
                    "memories": memory_count,
                    "identities": len(registry.entities),
                    "historical_memory_hydrated": True,
                    "private_recall": privacy_result,
                    "read_only_session": True,
                    "core_full_bootstrap_performed": False,
                    "writes_performed": False,
                    "discord_connected": False,
                    "production_cutover_authorized": False,
                }
        finally:
            db.close()
            if backend.opened_connections != 0:
                raise AivenReadOnlyProbeError("PG_READONLY_CONNECTION_LEAK")


def main() -> int:
    try:
        result = inspect()
    except AivenReadOnlyProbeError as exc:
        result = {"status": "blocked", "reason": str(exc)}
    except (PostgresRuntimeError,) as exc:
        code = str(exc)
        result = {
            "status": "blocked",
            "reason": code if re.fullmatch(r"PG_[A-Z0-9_]{1,80}", code)
            else "PG_READONLY_STORE_BLOCKED",
        }
    except Exception:
        # No underlying exception details; they can contain private content,
        # SQL parameters, paths, credentials or person IDs.
        result = {"status": "blocked", "reason": "PG_READONLY_UNEXPECTED_FAILURE"}
    result.setdefault("writes_performed", False)
    result.setdefault("production_cutover_authorized", False)
    print("AIVEN_CORE_CONTINUITY " + json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] == "real_staging_read_only_core_continuity_pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
