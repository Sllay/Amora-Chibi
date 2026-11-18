"""Run 2: native PostgreSQL 18 pg_dump / pg_restore in a disposable service.

This test NEVER invokes Docker outside the GitHub CI fixture; no external host,
backup file, DSN or production connection may be supplied. Fixed file name in
the isolated PostgreSQL CI service container only.
"""
from __future__ import annotations

from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
import os
import subprocess
from uuid import UUID

import pytest

from amora.events import PrivacyScope
from amora.identity import build_initial_registry
from amora.memory.models import MemoryKind,MemoryRecord,MemoryProvenance,ProvenanceKind
from amora.memory.postgres_store import PostgresMemoryStore
from amora.storage.postgres_memory_epoch_lab import rotate_pg_memory_epoch_after_restore
from amora.storage.postgres_runtime_registry import synthetic_core_backend
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.time import SimulatedClock

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1"
    or os.getenv("GITHUB_ACTIONS") != "true",
    reason="GitHub disposable PostgreSQL 18 service only",
)
NOW=datetime(2026,11,17,tzinfo=timezone.utc)
DUMP="/tmp/amora-run2-disposable-schema-v18.dump"


def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def _docker(*args):
    result=subprocess.run(
        ["docker",*args],check=True,capture_output=True,text=True,
        timeout=60,
    )
    return result.stdout.strip()


def _service_container():
    matching=_docker(
        "ps","--filter","ancestor=postgres:18-alpine",
        "--format","{{.ID}}",
    ).splitlines()
    assert len(matching)==1, "PG18_NATIVE_DUMP_SERVICE_NOT_UNIQUE"
    return matching[0]


def test_pg18_native_dump_restore_and_epoch_rotation(tmp_path:Path):
    service=_service_container()
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            status=prepare_disposable_core_v18(
                manager,include_memory_epoch_candidate=True,
                include_episode_candidate=True,
            )
            assert status["schema19_migration_performed"] is False
            backend=synthetic_core_backend(_raw,max_connections=2)
            id=str(UUID(int=554411))
            clock=SimulatedClock(NOW)
            path=tmp_path/"no-sqlite.db"
            with closing(PostgresMemoryStore(
                path,clock,build_initial_registry(),backend=backend,
            )) as memory:
                assert memory.put(MemoryRecord(
                    id=UUID(id),kind=MemoryKind.SEMANTIC,
                    content="Bela paisagem ficticia antes do backup",
                    provenance=MemoryProvenance(
                        kind=ProvenanceKind.OBSERVATION,
                        source="synthetic:pg-dump",actor_id="PERSON_BK",
                    ),
                    privacy_scope=PrivacyScope.PRIVATE,
                    owner_entity_id="PERSON_BK",
                    remote_processing_allowed=False,
                    created_at=NOW,updated_at=NOW,
                    importance=0.8,gate_score=0.8,
                    tags={"native","snapshot"},
                ),insert_only=True) is True
                before_stamp=memory.read_memory_projection_stamp()
                assert memory.get(id) is not None
            assert backend.opened_connections==0

            # The matching pg_dump/pg_restore binaries live inside the exact
            # postgres:18-alpine CI service. Never expose a variable DSN.
            _docker("exec",service,"pg_dump","-U","amora_ci",
                "-d","amora_pg_synthetic","-n","amora_pg_v18_lab",
                "--format=custom","--no-owner","--no-acl",
                "--file",DUMP)
            assert int(_docker("exec",service,"stat","-c","%s",DUMP))>1000

            # Restore only while isolated (no running Core/worker). Removing
            # the synthetic schema prevents silently overlaying stale data.
            manager.execute("DROP SCHEMA amora_pg_v18_lab CASCADE")
            _docker("exec",service,"pg_restore","-U","amora_ci",
                "-d","amora_pg_synthetic","--no-owner","--no-acl",
                "--exit-on-error",DUMP)

            # Restored snapshots retain old revision bytes. The restore
            # procedure MUST rotate the epoch before any replica reads.
            restored=manager.execute("""
                SELECT epoch_id,revision
                FROM amora_pg_v18_lab.amora_memory_projection_epoch
                WHERE singleton=1
            """).fetchone()
            assert restored==(before_stamp.epoch_id,before_stamp.revision)
            rotated=rotate_pg_memory_epoch_after_restore(manager)
            assert rotated!=before_stamp.epoch_id
            after=manager.execute("""
                SELECT epoch_id,revision
                FROM amora_pg_v18_lab.amora_memory_projection_epoch
                WHERE singleton=1
            """).fetchone()
            assert after[0]==rotated and after[1]>before_stamp.revision
            assert manager.execute(
                "SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records WHERE id=%s",
                (id,),
            ).fetchone()[0]==1
            assert manager.execute(
                "SELECT COUNT(*) FROM amora_pg_v18_lab.fts_term_postings WHERE memory_id=%s",
                (id,),
            ).fetchone()[0]>0
            assert manager.execute("""
                SELECT to_regclass('amora_pg_v18_lab.sensory_episode_jobs')
                    IS NOT NULL
            """).fetchone()[0] is True
            assert manager.execute("""
                SELECT COALESCE(MAX(version),0)
                FROM amora_pg_v18_lab.schema_migrations
            """).fetchone()[0]==18
            with closing(PostgresMemoryStore(
                path,clock,build_initial_registry(),backend=backend,
            )) as restored_store:
                assert restored_store.get(id).content==(
                    "Bela paisagem ficticia antes do backup"
                )
                assert restored_store.read_memory_projection_stamp().epoch_id==rotated
            assert backend.opened_connections==0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
            _docker("exec",service,"rm","-f",DUMP)
