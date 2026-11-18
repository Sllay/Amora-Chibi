"""Disposable PG18 integration: full verified snapshot staging, never Aiven."""
from __future__ import annotations

from contextlib import closing
import json
import os
import sqlite3
import zipfile

import pytest

from amora.storage.postgres_private_staging_import import (
    PrivateStagingImportError, import_private_staging_v18,
)
from amora.storage.postgres_runtime_connection import PostgresTargetPolicy
from amora.storage.postgres_v18_manifest import synthetic_v18_connection
from amora.storage.postgres_v18_lab_schema import install_pg_v18_lab
from amora.storage.postgres_fts_bm25_lab import install_bm25_lab
from amora.storage.postgres_voice_stop_lab import install_stop_table
from amora.storage.postgres_memory_epoch_lab import install_pg_memory_epoch_lab

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Only disposable localhost PostgreSQL 18 integration",
)


@pytest.fixture
def target():
    psycopg = pytest.importorskip("psycopg")
    with closing(psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )) as raw:
        raw.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            install_pg_v18_lab(raw)
            install_bm25_lab(raw)
            install_stop_table(raw)
            install_pg_memory_epoch_lab(raw)
            yield raw
        finally:
            raw.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")


@pytest.fixture
def pair(tmp_path):
    backup = tmp_path / "fake-private-pcs4c.db"
    with closing(synthetic_v18_connection()) as source:
        source.execute("""
            INSERT INTO entities(id,canonical_name,entity_type,payload_json)
            VALUES ('demo-a','Demo A','person','{}')
        """)
        source.execute("""
            INSERT INTO memory_records(
                id,kind,content,summary,provenance_kind,provenance_source,
                provenance_confidence,privacy_scope,owner_entity_id,
                audience_json,remote_processing_allowed,created_at,updated_at,
                importance,gate_score,metadata_json
            ) VALUES(
                'memory-demo','semantic','coelho floresta floresta',
                'coelho','observation','synthetic:staging',1.0,
                'PRIVATE','demo-a','["demo-a"]',0,
                '2026-11-18T00:00:00+00:00',
                '2026-11-18T00:00:00+00:00',0.8,0.8,'{"invented":true}'
            )
        """)
        source.execute(
            "INSERT INTO memory_fts(memory_id,content,tags) "
            "VALUES ('memory-demo','coelho floresta floresta','floresta')"
        )
        source.execute(
            "INSERT INTO memory_tags(memory_id,tag) "
            "VALUES ('memory-demo','floresta')"
        )
        source.execute(
            "INSERT INTO avatar_asset_blobs(file_name,body) VALUES (?,?)",
            ("fake-avatar.bin", b"\x00\xff\x01\x02"),
        )
        source.commit()
        with closing(sqlite3.connect(backup)) as target:
            source.backup(target)
    with zipfile.ZipFile(str(backup) + ".avatar.zip", "w") as z:
        z.writestr("manifest.json", json.dumps({"version": 1, "assets": []}))
    return backup


def _import(pair, target, confirm="IMPORT_VERIFIED_V18_TO_EMPTY_ISOLATED_STAGING"):
    return import_private_staging_v18(
        pair, target, PostgresTargetPolicy(), confirmation=confirm,
    )


def test_full_staged_private_shape_and_fts_token_parity(pair, target):
    result = _import(pair, target)
    assert result["status"] == "staged_import_completed"
    assert result["schema_version"] == 18
    assert result["relational_tables"] == 49
    assert result["typed_row_parity"] is True
    assert result["search_documents"] == 1
    assert result["search_postings"] >= 2
    assert result["production_cutover_authorized"] is False
    assert result["source_freshness"] == "not_certified"
    assert target.execute(
        "SELECT content,remote_processing_allowed FROM "
        "amora_pg_v18_lab.memory_records WHERE id='memory-demo'"
    ).fetchone() == ("coelho floresta floresta", 0)
    assert target.execute(
        "SELECT document_length FROM amora_pg_v18_lab.fts_doc_tokens "
        "WHERE memory_id='memory-demo'"
    ).fetchone()[0] == 4
    assert target.execute(
        "SELECT frequency FROM amora_pg_v18_lab.fts_term_postings "
        "WHERE memory_id='memory-demo' AND term='floresta'"
    ).fetchone()[0] == 3
    assert target.execute(
        "SELECT body FROM amora_pg_v18_lab.avatar_asset_blobs "
        "WHERE file_name='fake-avatar.bin'"
    ).fetchone()[0] == b"\x00\xff\x01\x02"
    assert target.execute(
        "SELECT COUNT(*) FROM amora_pg_v18_lab.amora_memory_projection_epoch"
    ).fetchone()[0] == 1


def test_no_approval_means_zero_target_reads_or_writes(pair, target):
    with pytest.raises(PrivateStagingImportError,
                       match="PG_PRIVATE_OPERATOR_CONFIRMATION_REQUIRED"):
        _import(pair, target, confirm="")
    assert target.execute(
        "SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records"
    ).fetchone()[0] == 0


def test_retry_on_nonempty_target_never_modifies_previous_import(pair, target):
    _import(pair, target)
    with pytest.raises(PrivateStagingImportError,
                       match="PG_PRIVATE_DESTINATION_NOT_EMPTY"):
        _import(pair, target)
    assert target.execute(
        "SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records"
    ).fetchone()[0] == 1


def test_missing_companion_archive_blocks_before_write(pair, target):
    os.unlink(str(pair) + ".avatar.zip")
    with pytest.raises(PrivateStagingImportError,
                       match="PG_PRIVATE_SOURCE_NOT_VERIFIED"):
        _import(pair, target)
    assert target.execute(
        "SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records"
    ).fetchone()[0] == 0


def test_missing_epoch_blocks_before_any_import(pair, target):
    target.execute(
        "DROP TABLE amora_pg_v18_lab.amora_memory_projection_epoch CASCADE"
    )
    with pytest.raises(PrivateStagingImportError,
                       match="PG_PRIVATE_TARGET_STRUCTURE_BLOCKED"):
        _import(pair, target)
    assert target.execute(
        "SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records"
    ).fetchone()[0] == 0
