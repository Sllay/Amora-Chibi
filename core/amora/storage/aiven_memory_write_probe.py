"""Gated one-shot Aiven staging memory write/reconnect/private recall.

Uses the SAME production MemoryStore adapter and v18 runtime SQL. It inserts
a fixed fictional PRIVATE record, reads it in a second connection, tests
unauthorized projection, and archives only that record afterward. Never
logs credentials, personal text, identities, raw SQL, or memory UUIDs.

This is staging verification, not serving/Discord or cutover authorization.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
from uuid import UUID

from amora.events import PrivacyScope
from amora.memory.models import (
    MemoryKind, MemoryProvenance, MemoryQuery, MemoryRecord, ProvenanceKind,
)
from amora.memory.postgres_store import PostgresMemoryStore
from amora.storage.aiven_core_readonly_probe import _settings
from amora.storage.database import AmoraDatabase
from amora.storage.postgres_run3_preflight import inspect_prepared_postgres
from amora.storage.postgres_runtime_connection import (
    PostgresBackendCandidate, PostgresRuntimeError,
)
from amora.storage.postgres_runtime_registry import compiled_domain_registry
from amora.time import SystemClock


_CONFIRMATION = "TEST_ONE_FICTIONAL_PRIVATE_MEMORY_ON_AIVEN_STAGING"
_BACKUP = Path("/private/pcs4c/backup/amora-aiven-pg18-v18-prewrite.dump")
_BACKUP_SIZE = 7106757
_PROBE_ID = UUID("c7d5a24a-5ba6-49a3-894e-3d4a1d20b276")
_PROBE_WORD = "zefiraflorestavioletanove"
_PROBE_CONTENT = _PROBE_WORD + " lembranca sintetica temporaria de persistencia"
_PROBE_MARKER = "aiven-staging-private-continuity-v1"
_OWNER = "PERSON_BK"
_UNRELATED = "AMORA_UNRELATED_PROBE_PRINCIPAL"


class WriteProbeBlocked(RuntimeError):
    pass


def _fail_if(condition: bool, code: str) -> None:
    if condition:
        raise WriteProbeBlocked(code)


def _query(recipient: str) -> MemoryQuery:
    return MemoryQuery(
        text=_PROBE_WORD,
        projection_recipient_id=recipient,
        require_topic_match=True,
    )


def _create_record() -> MemoryRecord:
    now = datetime.now(timezone.utc)
    return MemoryRecord(
        id=_PROBE_ID,
        kind=MemoryKind.EPISODIC,
        content=_PROBE_CONTENT,
        provenance=MemoryProvenance(
            kind=ProvenanceKind.OBSERVATION,
            source="synthetic:aiven-post-backup-check",
            actor_id=_OWNER,
        ),
        privacy_scope=PrivacyScope.PRIVATE,
        owner_entity_id=_OWNER,
        remote_processing_allowed=False,
        created_at=now,
        updated_at=now,
        importance=0.1,
        gate_score=0.9,
        metadata={"synthetic_test": _PROBE_MARKER},
    )


def _is_ours(item: MemoryRecord | None) -> bool:
    return (
        item is not None
        and item.id == _PROBE_ID
        and item.content == _PROBE_CONTENT
        and item.owner_entity_id == _OWNER
        and item.privacy_scope == PrivacyScope.PRIVATE
        and item.remote_processing_allowed is False
        and item.metadata.get("synthetic_test") == _PROBE_MARKER
    )


def continuity_with_backend(
    backend: PostgresBackendCandidate, *,
    expected_events: int,
    expected_identity_count: int,
    expected_prior_memories: int,
) -> dict[str, object]:
    """Can be tested with an injected disposable PostgreSQL 18 backend."""
    clock = SystemClock()
    path = Path("/tmp/amora-staging-memory-continuity-placeholder.sqlite")
    _fail_if(backend.opened_connections != 0, "PG_WRITE_PROBE_BACKEND_BUSY")

    with closing(AmoraDatabase(path, backend=backend)) as db:
        db.migrate()
        _fail_if(db.schema_version() != 18, "PG_WRITE_PROBE_SCHEMA_VERSION_DRIFT")
        _fail_if(db.event_count() != expected_events, "PG_WRITE_PROBE_HISTORY_CHANGED")
        registry = db.load_registry()
        _fail_if(
            registry is None or len(registry.entities) != expected_identity_count,
            "PG_WRITE_PROBE_IDENTITIES_CHANGED",
        )
        _fail_if(
            not any(person.id == _OWNER for person in registry.entities),
            "PG_WRITE_PROBE_OWNER_IDENTITY_UNAVAILABLE",
        )
    _fail_if(backend.opened_connections != 0, "PG_WRITE_PROBE_CONNECTION_LEAK")

    # Round 1: verify that original memories are present and write once.
    with closing(PostgresMemoryStore(
        path, clock, registry, backend=backend,
    )) as store:
        total_before = store.count(include_archived=True)
        _fail_if(
            total_before not in (expected_prior_memories, expected_prior_memories + 1),
            "PG_WRITE_PROBE_BASELINE_COUNT_DRIFT",
        )
        prior = store.get(_PROBE_ID)
        _fail_if(
            prior is not None and not _is_ours(prior),
            "PG_WRITE_PROBE_FIXED_ID_CONFLICT",
        )
        _fail_if(
            prior is not None and prior.archived_at is not None,
            "PG_WRITE_PROBE_ALREADY_FINISHED",
        )
        if prior is None:
            _fail_if(
                total_before != expected_prior_memories,
                "PG_WRITE_PROBE_UNEXPECTED_PREEXISTING_WRITE",
            )
            _fail_if(
                store.put(_create_record(), insert_only=True) is not True,
                "PG_WRITE_PROBE_INSERT_NOT_CONFIRMED",
            )
    _fail_if(backend.opened_connections != 0, "PG_WRITE_PROBE_CONNECTION_LEAK")

    # Round 2: a fresh server connection must retrieve it, check BM25 and ACL,
    # and archive only the known fictional record after validation.
    with closing(PostgresMemoryStore(
        path, clock, registry, backend=backend,
    )) as reopened:
        recovered = reopened.get(_PROBE_ID)
        _fail_if(not _is_ours(recovered), "PG_WRITE_PROBE_RECONNECT_READ_FAILED")
        _fail_if(
            not any(hit.memory.id == _PROBE_ID for hit in reopened.rank(_query(_OWNER))),
            "PG_WRITE_PROBE_OWNER_RECALL_FAILED",
        )
        _fail_if(
            any(hit.memory.id == _PROBE_ID for hit in reopened.rank(_query(_UNRELATED))),
            "PG_WRITE_PROBE_PRIVATE_MEMORY_DISCLOSED",
        )
        _fail_if(
            reopened.count(include_archived=True) != expected_prior_memories + 1,
            "PG_WRITE_PROBE_UNEXPECTED_MEMORY_COUNT",
        )
        _fail_if(
            reopened.archive(_PROBE_ID) is not True,
            "PG_WRITE_PROBE_TEST_MEMORY_ARCHIVE_FAILED",
        )
    _fail_if(backend.opened_connections != 0, "PG_WRITE_PROBE_CONNECTION_LEAK")

    # Round 3: after archiving, the test record is not eligible for normal
    # conversation recall; historical memory count is unchanged.
    with closing(PostgresMemoryStore(
        path, clock, registry, backend=backend,
    )) as final:
        _fail_if(
            final.count(include_archived=True) != expected_prior_memories + 1,
            "PG_WRITE_PROBE_FINAL_TOTAL_CHANGED",
        )
        _fail_if(
            final.count() > expected_prior_memories,
            "PG_WRITE_PROBE_TEST_MEMORY_STILL_ACTIVE",
        )
        _fail_if(
            any(hit.memory.id == _PROBE_ID for hit in final.rank(_query(_OWNER))),
            "PG_WRITE_PROBE_ARCHIVED_MEMORY_VISIBLE",
        )
        archived = final.get(_PROBE_ID)
        _fail_if(
            not _is_ours(archived) or archived.archived_at is None,
            "PG_WRITE_PROBE_ARCHIVE_NOT_DURABLE",
        )
    _fail_if(backend.opened_connections != 0, "PG_WRITE_PROBE_CONNECTION_LEAK")
    with closing(AmoraDatabase(path, backend=backend)) as db:
        _fail_if(db.event_count() != expected_events, "PG_WRITE_PROBE_HISTORY_CHANGED")
        _fail_if(db.schema_version() != 18, "PG_WRITE_PROBE_SCHEMA_VERSION_DRIFT")
    _fail_if(backend.opened_connections != 0, "PG_WRITE_PROBE_CONNECTION_LEAK")
    return {
        "status": "aiven_staging_new_memory_write_reconnect_pass",
        "old_memory_count": expected_prior_memories,
        "identity_count": expected_identity_count,
        "old_event_count": expected_events,
        "fictional_memory_written": True,
        "fresh_connection_hydration_pass": True,
        "owner_recall_pass": True,
        "unrelated_principal_rejected": True,
        "fictional_memory_archived_after_test": True,
        "historical_memories_not_archived_by_test": True,
        "discord_connected": False,
        "production_cutover_authorized": False,
    }


def run() -> dict[str, object]:
    """Only called after CLI confirmation and independent backup verification."""
    _fail_if(
        not _BACKUP.is_file()
        or _BACKUP.stat().st_size != _BACKUP_SIZE,
        "PG_WRITE_PROBE_VERIFIED_PREWRITE_BACKUP_MISSING",
    )
    dsn, ca_file, target = _settings()
    try:
        import psycopg
    except ImportError:
        raise WriteProbeBlocked("PG_WRITE_PROBE_DRIVER_MISSING") from None

    def connect(*, read_only: bool = False):
        try:
            raw = psycopg.connect(
                dsn, sslmode="verify-full", sslrootcert=ca_file,
                autocommit=True, connect_timeout=8,
                application_name="amora-one-shot-fake-memory-test",
                options=(
                    "-c default_transaction_read_only=on -c search_path=pg_catalog"
                    if read_only else "-c search_path=pg_catalog"
                ),
            )
            target.verify(raw)
            return raw
        except Exception:
            raise WriteProbeBlocked("PG_WRITE_PROBE_CONNECT_OR_TARGET_BLOCKED") from None

    with closing(connect(read_only=True)) as raw:
        _fail_if(
            raw.execute(
                "SELECT current_setting('default_transaction_read_only')"
            ).fetchone()[0] != "on",
            "PG_WRITE_PROBE_PREFLIGHT_NOT_READ_ONLY",
        )
        structure = inspect_prepared_postgres(raw, target)
        _fail_if(
            structure.status != "structural_pass",
            "PG_WRITE_PROBE_STAGING_STRUCTURE_BLOCKED",
        )
        memories, events, entities = raw.execute(
            "SELECT "
            "(SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records),"
            "(SELECT COUNT(*) FROM amora_pg_v18_lab.events),"
            "(SELECT COUNT(*) FROM amora_pg_v18_lab.entities)"
        ).fetchone()
        _fail_if(
            (memories, events, entities) not in (
                (88, 11388, 6), (89, 11388, 6),
            ),
            "PG_WRITE_PROBE_BASELINE_UNEXPECTED",
        )
    backend = PostgresBackendCandidate(
        lambda: connect(read_only=False),
        compiled_domain_registry(),
        target=target, max_connections=3,
    )
    return continuity_with_backend(
        backend, expected_events=11388, expected_identity_count=6,
        expected_prior_memories=88,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Guarded Aiven staging memory test")
    parser.add_argument("--confirm", default="")
    args = parser.parse_args(argv)
    try:
        _fail_if(
            args.confirm != _CONFIRMATION,
            "PG_WRITE_PROBE_CONFIRMATION_REQUIRED",
        )
        report = run()
    except WriteProbeBlocked as exc:
        report = {"status": "blocked", "reason": str(exc)}
    except (PostgresRuntimeError,) as exc:
        value = str(exc)
        report = {
            "status": "blocked",
            "reason": value if value.startswith("PG_") else "PG_WRITE_PROBE_RUNTIME_BLOCKED",
        }
    except Exception:
        report = {"status": "blocked", "reason": "PG_WRITE_PROBE_UNKNOWN_FAILURE"}
    report.setdefault("production_cutover_authorized", False)
    print("AIVEN_MEMORY_WRITE_TEST " + json.dumps(report, sort_keys=True), flush=True)
    return 0 if report["status"] == "aiven_staging_new_memory_write_reconnect_pass" else 2


if __name__ == "__main__":
    raise SystemExit(main())
