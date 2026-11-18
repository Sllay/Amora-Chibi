"""Run 3 structural gates: no real Aiven, secret, migration or writes."""
from __future__ import annotations

from contextlib import closing
import os

import pytest

from amora.storage.postgres_run3_preflight import inspect_prepared_postgres
from amora.storage.postgres_runtime_connection import PostgresTargetPolicy
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Requires disposable localhost PostgreSQL 18 only",
)

def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_pg18_run3_readonly_preflight_pass_is_never_cutover_approval():
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(
                manager,include_memory_epoch_candidate=True,
            )
            first=inspect_prepared_postgres(manager,PostgresTargetPolicy())
            second=inspect_prepared_postgres(manager,PostgresTargetPolicy())
            assert first==second
            assert first.status=="structural_pass"
            assert first.schema_version==18
            assert first.relational_tables>=53
            assert first.blockers==()
            assert len(first.checks)>=7
            assert first.production_ready is False
            assert first.as_dict()["production_ready"] is False
            assert "host" not in first.as_dict()
            assert "password" not in first.as_dict()
            assert manager.execute("""
                SELECT COUNT(*) FROM amora_pg_v18_lab.amora_memory_projection_epoch
                WHERE singleton=1 AND revision=0
            """).fetchone()[0]==1
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")


def test_missing_epoch_and_index_reject_cutover_even_when_v18_exists():
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            missing=inspect_prepared_postgres(manager,PostgresTargetPolicy())
            assert missing.status=="blocked"
            assert "PG_PREFLIGHT_EPOCH_NOT_INSTALLED" in missing.blockers
            assert missing.production_ready is False
            assert inspect_prepared_postgres(
                manager,PostgresTargetPolicy(),require_memory_epoch=False,
            ).status=="structural_pass"
            manager.execute(
                "DROP INDEX amora_pg_v18_lab.fts_term_postings_lookup"
            )
            broken=inspect_prepared_postgres(
                manager,PostgresTargetPolicy(),require_memory_epoch=False,
            )
            assert broken.status=="blocked"
            assert "PG_PREFLIGHT_FTS_INDEX_MISSING" in broken.blockers
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")


def test_schema_column_drift_rejected_without_mutation_or_sensitive_values():
    with closing(_raw()) as manager:
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(
                manager,include_memory_epoch_candidate=True,
            )
            manager.execute("""
                ALTER TABLE amora_pg_v18_lab.memory_records
                ADD COLUMN unexpected_run3_column TEXT
            """)
            blocked=inspect_prepared_postgres(manager,PostgresTargetPolicy())
            assert blocked.status=="blocked"
            assert "PG_PREFLIGHT_COLUMN_TYPE_DRIFT" in blocked.blockers
            assert blocked.production_ready is False
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")


def test_remote_target_cannot_be_claimed_from_local_connection():
    with closing(_raw()) as raw:
        policy=PostgresTargetPolicy(
            host="aiven.invalid",database="amora_pg_synthetic",
            schema="amora_pg_v18_lab",require_tls=True,
        )
        result=inspect_prepared_postgres(raw,policy)
        assert result.status=="blocked"
        assert result.blockers==("PG_PREFLIGHT_TARGET_NOT_VERIFIED",)
        assert result.checks==()
        assert result.production_ready is False
