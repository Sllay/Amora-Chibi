"""Full original AmoraCore bootstrap against ONLY an isolated PG18 restore.

The real user's encrypted backup is decrypted by GitHub Actions solely on an
ephemeral runner and restored to localhost. The live Aiven and Northflank
services are never opened by this program. Emits *only* redacted statuses.
"""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import secrets
import sys


_STAGE = "entry"


def run() -> dict[str, object]:
    global _STAGE
    _STAGE = "sandbox_confirmation"
    if os.environ.get("AMORA_RESTORED_SANDBOX_ONLY") != "1":
        raise RuntimeError("SANDBOX_OPERATOR_APPROVAL_REQUIRED")

    # Hardcoded loopback URI. No Aiven DSN, token, CA or network connection is
    # read from the environment. The runner owns a disposable PostgreSQL 18.
    import psycopg
    from amora.app import AmoraCore
    from amora.storage.postgres_runtime_registry import synthetic_core_backend

    def database():
        return psycopg.connect(
            host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
            user="amora_ci", password="synthetic_only",
            autocommit=True, connect_timeout=5,
        )

    _STAGE = "restored_record_counters"
    with closing(database()) as before:
        num, identities, events, memories = before.execute(
            "SELECT "
            "(SELECT MAX(version) FROM amora_pg_v18_lab.schema_migrations),"
            "(SELECT COUNT(*) FROM amora_pg_v18_lab.entities),"
            "(SELECT COUNT(*) FROM amora_pg_v18_lab.events),"
            "(SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records)"
        ).fetchone()
        if (num, identities, events, memories) != (18, 6, 11388, 88):
            raise RuntimeError("RESTORED_SOURCE_COUNTS_MISMATCH")
        first_id = before.execute(
            "SELECT id FROM amora_pg_v18_lab.memory_records ORDER BY id LIMIT 1"
        ).fetchone()[0]

    os.environ["AMORA_MODE"] = "cloud"
    os.environ["AMORA_ASSET_STORAGE"] = "database"
    os.environ["AMORA_HOME"] = "/tmp/amora-restored-core-only"
    os.environ["AMORA_SECRET_LOCAL_API_TOKEN"] = secrets.token_hex(32)
    os.environ["AMORA_SOCIAL_PRIMARY"] = "groq"
    os.environ["AMORA_PUBLIC_ORIGINS"] = "https://amora-restored-sandbox.invalid"
    os.environ.pop("AMORA_POSTGRES_DSN", None)
    os.environ.pop("AMORA_SECRET_NUMES_API_KEY", None)
    os.environ.pop("AMORA_SECRET_GROQ_API_KEY", None)

    _STAGE = "first_core_bootstrap"
    backend = synthetic_core_backend(database, max_connections=16)
    first = AmoraCore.bootstrap(storage=backend)
    try:
        _STAGE = "first_core_memory_identity"
        if first.database.schema_version() != 18:
            raise RuntimeError("RESTORED_CORE_SCHEMA_FAILED")
        if len(first.identities.entities) != identities:
            raise RuntimeError("RESTORED_CORE_IDENTITIES_CHANGED")
        historical = first.memory.store.get(first_id)
        if historical is None or str(historical.id) != str(first_id):
            raise RuntimeError("RESTORED_CORE_HISTORICAL_MEMORY_MISSING")
        if first.memory.store.count(include_archived=True) != memories:
            raise RuntimeError("RESTORED_CORE_MEMORIES_CHANGED")
        if first.database.event_count() != events:
            raise RuntimeError("RESTORED_CORE_EVENTS_CHANGED")
        _STAGE = "first_core_conversation_state"
        state = first.conversation.state()
        if state is None:
            raise RuntimeError("RESTORED_CORE_CONVERSATION_MISSING")
        _STAGE = "first_core_api_factory"
        from amora.api import create_api
        api = create_api(first, own_core=False)
        if api is None:
            raise RuntimeError("RESTORED_CORE_API_FACTORY_FAILED")
    finally:
        first.close()
    if backend.opened_connections != 0:
        raise RuntimeError("RESTORED_CORE_CONNECTION_LEAK")

    _STAGE = "second_core_bootstrap"
    second = AmoraCore.bootstrap(storage=backend)
    try:
        _STAGE = "second_core_history_recovery"
        if second.memory.store.get(first_id) is None:
            raise RuntimeError("RESTORED_CORE_MEMORY_LOST_AFTER_RESTART")
        if second.memory.store.count(include_archived=True) != memories:
            raise RuntimeError("RESTORED_CORE_RESTART_COUNT_DRIFT")
        if second.database.event_count() != events:
            raise RuntimeError("RESTORED_CORE_RESTART_EVENTS_CHANGED")
        if len(second.identities.entities) != identities:
            raise RuntimeError("RESTORED_CORE_RESTART_IDENTITY_DRIFT")
    finally:
        second.close()
    if backend.opened_connections != 0:
        raise RuntimeError("RESTORED_CORE_RESTART_CONNECTION_LEAK")

    return {
        "status": "isolated_restored_full_core_bootstrap_pass",
        "schema_version": 18,
        "historical_memories": memories,
        "history_events": events,
        "identities": identities,
        "core_started_twice": True,
        "historical_memory_hydrated_after_restart": True,
        "core_conversation_state_loaded": True,
        "api_factory_created": True,
        "backend_connections_released": True,
        "live_aiven_touched": False,
        "discord_connected": False,
        "production_cutover_authorized": False,
    }


def main() -> int:
    try:
        result = run()
    except Exception:
        # Real imported memory contents, path and credentials must NEVER be
        # reflected via SQL exception text or Core errors in Action logs.
        result = {
            "status": "blocked",
            "reason": "RESTORED_CORE_BOOTSTRAP_NEEDS_REVIEW",
            "phase": _STAGE,
            "live_aiven_touched": False,
            "production_cutover_authorized": False,
        }
    print("AIVEN_RESTORED_CORE " + json.dumps(result, sort_keys=True), flush=True)
    return 0 if result["status"] == "isolated_restored_full_core_bootstrap_pass" else 2


if __name__ == "__main__":
    sys.exit(main())
