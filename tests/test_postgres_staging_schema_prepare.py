"""One-shot staging schema preparation, exercised only on disposable PG18."""
from __future__ import annotations

from contextlib import closing
import json
import os
import sqlite3
import zipfile

import pytest

from amora.storage.postgres_staging_schema_prepare import (
    StagingSchemaError, prepare_empty_staging_schema,
)
from amora.storage.postgres_run3_preflight import inspect_prepared_postgres
from amora.storage.postgres_private_staging_import import import_private_staging_v18
from amora.storage.postgres_runtime_connection import PostgresTargetPolicy
from amora.storage.postgres_v18_manifest import synthetic_v18_connection

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Requires disposable localhost PG18; never Aiven credentials",
)

_CONFIRM = "PREPARE_EMPTY_PG18_ISOLATED_STAGING_ONLY"
_SCHEMA = "amora_pg_v18_lab"


@pytest.fixture
def pg():
    psycopg = pytest.importorskip("psycopg")
    with closing(psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )) as raw:
        raw.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            yield raw
        finally:
            raw.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")


def _prepare(pg, *, confirm=_CONFIRM, target=None):
    return prepare_empty_staging_schema(
        pg, target or PostgresTargetPolicy(), confirmation=confirm,
    )


def _exists(pg):
    return pg.execute(
        "SELECT COUNT(*) FROM pg_catalog.pg_namespace WHERE nspname=%s",
        (_SCHEMA,),
    ).fetchone()[0] == 1


def test_prepared_empty_schema_has_fences_but_cannot_serve(pg):
    r = _prepare(pg)
    assert r["status"] == "empty_staging_schema_prepared"
    assert r["source_tables"] == 51
    assert r["relational_tables"] == 49
    assert r["canonical_indexes"] == 65
    assert r["auxiliary_tables_prepared"] == 4
    assert r["private_import_performed"] is False
    assert r["production_cutover_authorized"] is False
    assert r["postcommit_readonly_preflight"] == "preimport_structural_pass"
    policy = PostgresTargetPolicy()
    assert inspect_prepared_postgres(pg, policy).status == "blocked"
    assert inspect_prepared_postgres(
        pg, policy, allow_empty_migrations_for_import=True,
    ).status == "preimport_structural_pass"
    assert pg.execute(
        "SELECT COUNT(*) FROM amora_pg_v18_lab.schema_migrations"
    ).fetchone()[0] == 0
    assert pg.execute(
        "SELECT revision FROM amora_pg_v18_lab.amora_memory_projection_epoch"
    ).fetchone()[0] == 0


def test_no_confirmation_prevents_ddl_before_any_write(pg):
    with pytest.raises(
        StagingSchemaError, match="PG_STAGING_OPERATOR_CONFIRMATION_REQUIRED",
    ):
        _prepare(pg, confirm="")
    assert not _exists(pg)


def test_existing_schema_is_never_recreated_or_replaced(pg):
    _prepare(pg)
    with pytest.raises(
        StagingSchemaError, match="PG_STAGING_SCHEMA_ALREADY_EXISTS",
    ):
        _prepare(pg)
    assert _exists(pg)
    assert pg.execute(
        "SELECT COUNT(*) FROM amora_pg_v18_lab.schema_migrations"
    ).fetchone()[0] == 0


def test_shared_database_with_other_user_tables_is_not_accepted(pg):
    pg.execute("CREATE TABLE public.amora_staging_foreign_fixture(id integer)")
    try:
        with pytest.raises(
            StagingSchemaError, match="PG_STAGING_DATABASE_NOT_ISOLATED",
        ):
            _prepare(pg)
        assert not _exists(pg)
    finally:
        pg.execute("DROP TABLE public.amora_staging_foreign_fixture")


def test_remote_policy_mismatch_does_not_approve_local_connection(pg):
    policy = PostgresTargetPolicy(
        host="demo.f.aivencloud.com", port=14600,
        database="defaultdb", schema=_SCHEMA, require_tls=True,
    )
    with pytest.raises(
        StagingSchemaError, match="PG_STAGING_TARGET_NOT_VERIFIED",
    ):
        _prepare(pg, target=policy)
    assert not _exists(pg)


def test_non_aiven_remote_policy_blocks_before_target_verification(pg):
    policy = PostgresTargetPolicy(
        host="postgres.example.invalid", database="defaultdb",
        schema=_SCHEMA, require_tls=True,
    )
    with pytest.raises(
        StagingSchemaError, match="PG_STAGING_REMOTE_TARGET_NOT_AIVEN_TLS",
    ):
        _prepare(pg, target=policy)
    assert not _exists(pg)


def test_staging_preparation_then_real_shape_import_then_serving_preflight(pg, tmp_path):
    """No real user data; connect separately built pieces on fabricated v18."""
    backup = tmp_path / "synthetic-pcs4c.db"
    with closing(synthetic_v18_connection()) as source:
        source.commit()
        with closing(sqlite3.connect(backup)) as destination:
            source.backup(destination)
    with zipfile.ZipFile(str(backup) + ".avatar.zip", "w") as z:
        z.writestr("manifest.json", json.dumps({"version": 1, "assets": []}))
    _prepare(pg)
    imported = import_private_staging_v18(
        backup, pg, PostgresTargetPolicy(),
        confirmation="IMPORT_VERIFIED_V18_TO_EMPTY_ISOLATED_STAGING",
    )
    assert imported["status"] == "staged_import_completed"
    assert imported["typed_row_parity"] is True
    assert imported["production_cutover_authorized"] is False
    assert inspect_prepared_postgres(pg, PostgresTargetPolicy()).status == "structural_pass"
