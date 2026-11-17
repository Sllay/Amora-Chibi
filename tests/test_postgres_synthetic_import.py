"""Live PG18 test: copy synthetic SQLite v18 rows, never user backups."""
from contextlib import closing
import os

import pytest

from amora.storage import postgres_memory_lab as guard
from amora.storage import postgres_synthetic_import as importer
from amora.storage import postgres_v18_lab_schema as pg_schema
from amora.storage import postgres_v18_manifest as source_schema

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Disposable loopback PostgreSQL 18 only",
)


@pytest.fixture
def pg():
    psycopg = pytest.importorskip("psycopg")
    con = psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )
    try:
        guard.ensure_local_lab(con)
        con.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        pg_schema.install_pg_v18_lab(con)
        yield con
    finally:
        try:
            con.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        finally:
            con.close()


@pytest.fixture
def sqlite_source():
    with closing(source_schema.synthetic_v18_connection()) as con:
        con.execute("""
            INSERT INTO entities(id, canonical_name, entity_type, payload_json)
            VALUES ('person-a','Person A','person','{}'),
                   ('person-b','Person B','person','{}')
        """)
        con.execute("""
            INSERT INTO relationships(source,relation_type,target,confidence)
            VALUES ('person-a','companion','person-b',0.75)
        """)
        con.execute("""
            INSERT INTO memory_records (
                id, kind, content, summary, provenance_kind,
                provenance_source, provenance_confidence,
                privacy_scope, owner_entity_id, audience_json,
                remote_processing_allowed, created_at, updated_at,
                importance, gate_score, metadata_json
            ) VALUES (
                'mem-1','semantic','coelho da floresta','coelho',
                'observation','synthetic:pg-lab',1.0,
                'PERSONAL','person-a','["person-a"]',1,
                '2026-11-17T00:00:00+00:00','2026-11-17T00:00:00+00:00',
                0.8,0.7,'{"turn_id":"synthetic"}'
            )
        """)
        con.execute("""
            INSERT INTO memory_tags(memory_id,tag) VALUES ('mem-1','floresta')
        """)
        con.execute("""
            INSERT INTO memory_entities(memory_id,entity_id,confidence)
            VALUES ('mem-1','person-a',0.9)
        """)
        con.execute("""
            INSERT INTO memory_fts(memory_id,content,tags)
            VALUES ('mem-1','coelho da floresta coelho','floresta')
        """)
        con.execute(
            "INSERT INTO avatar_asset_blobs(file_name,body) VALUES (?,?)",
            ("synthetic-test-asset.bin", b"\x00\xff\x01\x02synthetic"),
        )
        con.commit()
        yield con


def test_exact_synthetic_49_table_import_with_fts_and_identity(pg, sqlite_source):
    before = sqlite_source.execute("PRAGMA integrity_check").fetchone()
    report = importer.import_synthetic_v18(sqlite_source, pg)
    assert report["status"] == "ok"
    assert report["schema_version"] == 18
    assert report["relational_tables"] == 49
    assert report["relational_rows"] >= 22
    assert report["fts_rows"] == 1
    assert report["typed_row_parity"] is True
    assert report["identity_sequences_reconciled"] >= 1
    assert report["real_backup_read"] is False
    assert report["production_backend_changed"] is False
    assert sqlite_source.execute("PRAGMA integrity_check").fetchone() == before
    assert pg.execute("""
        SELECT content,metadata_json FROM amora_pg_v18_lab.memory_records
        WHERE id='mem-1'
    """).fetchone() == ("coelho da floresta", '{"turn_id":"synthetic"}')
    assert pg.execute("""
        SELECT memory_id FROM amora_pg_v18_lab.memory_search
    """).fetchone() == ("mem-1",)
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.memory_tags WHERE tag='floresta'
    """).fetchone() == (1,)
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.relationships
    """).fetchone() == (1,)
    assert pg.execute("""
        SELECT body FROM amora_pg_v18_lab.avatar_asset_blobs
        WHERE file_name='synthetic-test-asset.bin'
    """).fetchone() == (b"\x00\xff\x01\x02synthetic",)


def test_nonempty_target_rejected_without_new_write(pg, sqlite_source):
    importer.import_synthetic_v18(sqlite_source, pg)
    with pytest.raises(importer.SyntheticImportError, match="SYNTHETIC_TARGET_NOT_EMPTY"):
        importer.import_synthetic_v18(sqlite_source, pg)
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records
    """).fetchone() == (1,)


def test_unknown_source_schema_rejected_before_import(pg, sqlite_source):
    sqlite_source.execute("CREATE TABLE unreviewed_private(id TEXT)")
    with pytest.raises(importer.SyntheticImportError, match="SYNTHETIC_SOURCE_TABLES"):
        importer.import_synthetic_v18(sqlite_source, pg)
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records
    """).fetchone() == (0,)


def test_fails_closed_before_any_pg_sql_with_remote_destination(sqlite_source):
    from types import SimpleNamespace

    class Remote:
        def __init__(self):
            self.info = SimpleNamespace(
                host="example.aivencloud.com", dbname="amora_pg_synthetic",
                server_version=180000,
            )
        def execute(self, *a, **kw):
            raise AssertionError("Never execute SQL on remote PostgreSQL")
    with pytest.raises(ValueError, match="POSTGRES_LAB_LOCAL_DB_REQUIRED"):
        importer.import_synthetic_v18(sqlite_source, Remote())


def test_invalid_fts_foreign_key_rolls_back_entire_import(pg, sqlite_source):
    psycopg = pytest.importorskip("psycopg")
    # FTS5 has no relational FK in SQLite. A stale reference must NOT be
    # imported as a partially committed PostgreSQL memory graph.
    sqlite_source.execute("""
        INSERT INTO memory_fts(memory_id,content,tags)
        VALUES ('orphan-fts','orphan synthetic text','test')
    """)
    sqlite_source.commit()
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        importer.import_synthetic_v18(sqlite_source, pg)
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records
    """).fetchone() == (0,)
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.entities
    """).fetchone() == (0,)
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.memory_search
    """).fetchone() == (0,)


def test_batches_and_identity_sequence_preserved(pg, sqlite_source):
    sqlite_source.executemany(
        "INSERT INTO memory_tags(memory_id,tag) VALUES ('mem-1',?)",
        [(f"tag-{i:03d}",) for i in range(250)],
    )
    sqlite_source.commit()
    report = importer.import_synthetic_v18(sqlite_source, pg)
    assert report["typed_row_parity"] is True
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.memory_tags
    """).fetchone() == (251,)
    # The relational SQLite autoincrement high-water mark must survive.
    next_id = pg.execute("""
        SELECT nextval(pg_get_serial_sequence('amora_pg_v18_lab.relationships', 'id'))
    """).fetchone()[0]
    assert next_id == 2


def test_source_index_drift_is_rejected_before_any_pg_insert(pg, sqlite_source):
    sqlite_source.execute("DROP INDEX idx_memory_kind_created")
    with pytest.raises(importer.SyntheticImportError,
                       match="SYNTHETIC_SOURCE_INDEX_DRIFT"):
        importer.import_synthetic_v18(sqlite_source, pg)
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records
    """).fetchone() == (0,)
