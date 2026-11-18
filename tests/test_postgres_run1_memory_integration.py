"""Run 1: real PostgresMemoryStore privacy and lexical recall smoke.

Local disposable PostgreSQL 18 only, with wholly invented memories. Never
runs against PCS.4C, Turso or Aiven.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
from uuid import UUID

import pytest

from amora.events import PrivacyScope
from amora.identity import build_initial_registry
from amora.memory.models import (
    MemoryKind, MemoryProvenance, MemoryQuery, MemoryRecord, ProvenanceKind,
)
from amora.memory.postgres_store import PostgresMemoryStore
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Only disposable local PostgreSQL 18 is permitted",
)
NOW = datetime(2026, 11, 17, tzinfo=timezone.utc)


def _connect():
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )


def test_pg_memory_write_scoped_recall_and_restart(tmp_path: Path):
    with closing(_connect()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            preparation = prepare_disposable_core_v18(manager)
            assert preparation["production_backend_switched"] is False
            backend = synthetic_core_backend(_connect, max_connections=2)
            clock = SimulatedClock(NOW)
            identities = build_initial_registry()
            path = tmp_path / "not-a-sqlite-db.db"
            record = MemoryRecord(
                id=UUID(int=9281),
                kind=MemoryKind.EPISODIC,
                content="selenioficticio77 registo inventado do jardim",
                provenance=MemoryProvenance(
                    kind=ProvenanceKind.OBSERVATION,
                    source="synthetic-run1", actor_id="PERSON_BK",
                ),
                privacy_scope=PrivacyScope.PRIVATE,
                owner_entity_id="PERSON_BK",
                remote_processing_allowed=False,
                tags={"synthetic", "garden"},
                created_at=NOW, updated_at=NOW,
                importance=0.8, gate_score=0.9,
            )
            permitted = MemoryQuery(
                text="selenioficticio77",
                projection_recipient_id="PERSON_BK",
            )
            forbidden = MemoryQuery(
                text="selenioficticio77",
                projection_recipient_id="PERSON_OTHER",
            )
            remote = MemoryQuery(
                text="selenioficticio77",
                projection_recipient_id="PERSON_BK",
                projection_remote_only=True,
            )
            with closing(PostgresMemoryStore(
                path, clock, identities, backend=backend,
            )) as memory:
                assert memory.put(record, insert_only=True) is True
                assert memory.put(record, insert_only=True) is False
                found = memory.rank(permitted)
                assert any(item.memory.id == record.id for item in found)
                assert memory.rank(forbidden) == []
                assert memory.rank(remote) == []
                assert memory.get(record.id).tags == record.tags

            with closing(PostgresMemoryStore(
                path, clock, identities, backend=backend,
            )) as restarted:
                assert any(item.memory.id == record.id
                           for item in restarted.rank(permitted))
                assert restarted.archive(record.id) is True
                assert restarted.rank(permitted) == []
                assert restarted.rank(forbidden) == []
            assert backend.opened_connections == 0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")


def test_pg_owner_recall_finds_old_indexed_memory_beyond_512_newer_rows(tmp_path: Path):
    """A scoped FTS lookup must not miss an old memory behind 8 pages."""
    from datetime import timedelta

    with closing(_connect()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            backend = synthetic_core_backend(_connect, max_connections=2)
            clock = SimulatedClock(NOW)
            owner = "PERSON_BK"
            secret = MemoryRecord(
                id=UUID(int=9290),
                kind=MemoryKind.EPISODIC,
                content="zeldoino77 historia privada da infancia",
                provenance=MemoryProvenance(
                    kind=ProvenanceKind.OBSERVATION,
                    source="synthetic:old-memory",actor_id=owner,
                ),
                privacy_scope=PrivacyScope.PRIVATE,
                owner_entity_id=owner,
                remote_processing_allowed=False,
                tags={"infancia"},
                created_at=NOW-timedelta(days=400),
                updated_at=NOW-timedelta(days=400),
                importance=0.8,gate_score=0.9,
            )
            with closing(PostgresMemoryStore(
                tmp_path / "not-sqlite.db",clock,
                build_initial_registry(),backend=backend,
            )) as memory:
                assert memory.put(secret,insert_only=True) is True
                # Bypass postings only for the unrelated noise documents: the
                # old target is indexed through the real MemoryStore writer.
                with manager.transaction():
                    with manager.cursor() as cursor:
                        cursor.executemany("""
                            INSERT INTO amora_pg_v18_lab.memory_records(
                                id,kind,content,provenance_kind,
                                provenance_source,provenance_confidence,
                                privacy_scope,owner_entity_id,
                                created_at,updated_at,importance,gate_score
                            ) VALUES (
                                %s,'episodic','novo registro irrelevante',
                                'observation','synthetic:noise',1,
                                'PRIVATE',%s,%s,%s,0.8,0.9
                            )
                        """, [
                            (
                                f"synthetic-noise-{i:04d}",owner,
                                (NOW-timedelta(seconds=i)).isoformat(),
                                (NOW-timedelta(seconds=i)).isoformat(),
                            )
                            for i in range(540)
                        ])
                assert memory.count() >= 541
                recalled, complete = memory.recall_owner_records(
                    owner,"zeldoino77",limit=3,
                )
                assert [item.id for item in recalled] == [secret.id]
                # Deliberately unindexed noise means full legacy coverage is
                # not provable after the bounded fallback. Report incomplete
                # rather than falsely claiming an exhaustive lookup.
                assert complete is False
                assert memory.recall_owner_records(
                    "PERSON_OTHER","zeldoino77",
                )[0] == []
                assert memory.recall_owner_records(
                    owner,"zeldoino77",public_only=True,
                )[0] == []
                assert memory.recall_owner_records(
                    owner,"zeldoino77",share_only=True,
                )[0] == []
            assert backend.opened_connections == 0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
