"""PostgreSQL 18 full v18 *empty schema* integration gates, synthetic CI only."""
import os
import sqlite3

import pytest

from amora.storage import postgres_v18_manifest as manifest
from amora.storage import postgres_v18_lab_schema as compiler
from amora.storage import postgres_memory_lab

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Requires disposable PostgreSQL 18 local CI service",
)


@pytest.fixture
def pg():
    psycopg = pytest.importorskip("psycopg")
    conn = psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )
    try:
        postgres_memory_lab.ensure_local_lab(conn)
        conn.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        yield conn
    finally:
        try:
            conn.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        finally:
            conn.close()


def test_all_v18_relational_tables_indices_and_columns_exist_in_postgres(pg):
    expected = manifest.build_manifest()
    report = compiler.install_pg_v18_lab(pg)
    assert report["source_tables"] == 51
    assert report["postgres_source_tables"] == 49
    assert report["source_indexes"] == 65
    assert report["postgres_search_replacement"] is True

    table_rows = pg.execute("""
        SELECT tablename FROM pg_tables WHERE schemaname='amora_pg_v18_lab'
        ORDER BY tablename
    """).fetchall()
    names = {x[0] for x in table_rows}
    source = {t["name"]: t for t in expected["tables"] if t["kind"] == "ordinary"}
    assert names == {*source, "memory_search"}
    assert "sqlite_sequence" not in names
    assert "memory_fts" not in names

    for table, descriptor in source.items():
        pg_fields = {
            c: t.lower() for c, t in pg.execute("""
                SELECT column_name, data_type FROM information_schema.columns
                WHERE table_schema='amora_pg_v18_lab' AND table_name=%s
            """, (table,)).fetchall()
        }
        assert pg_fields == {
            c["name"]: c["postgres_type"] for c in descriptor["columns"]
        }, table

    explicit_indexes = {
        name for (name,) in pg.execute("""
            SELECT indexname FROM pg_indexes WHERE schemaname='amora_pg_v18_lab'
        """).fetchall()
    }
    assert {x["name"] for x in expected["indexes"]} <= explicit_indexes
    assert "amora_v18_memory_search_gin" in explicit_indexes

    constraints = dict(pg.execute("""
        SELECT contype::text, count(*) FROM pg_constraint
        WHERE connamespace='amora_pg_v18_lab'::regnamespace
        GROUP BY contype
    """).fetchall())
    assert constraints["f"] == report["foreign_keys"] + 1  # search document FK
    assert constraints["c"] == report["source_checks"]
    assert constraints["u"] >= report["source_unique"]


def test_full_schema_is_empty_and_fk_enforced(pg):
    psycopg = pytest.importorskip("psycopg")
    compiler.install_pg_v18_lab(pg)
    for (name,) in pg.execute("""
        SELECT tablename FROM pg_tables WHERE schemaname='amora_pg_v18_lab'
    """):
        assert pg.execute(
            "SELECT count(*) FROM " + compiler._ref(name)
        ).fetchone()[0] == 0
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        with pg.transaction():
            pg.execute("""
                INSERT INTO amora_pg_v18_lab.memory_entities
                     (memory_id, entity_id, confidence)
                VALUES ('missing','entity',0.5)
            """)
    assert pg.execute("""
        SELECT count(*) FROM amora_pg_v18_lab.memory_entities
    """).fetchone() == (0,)


def test_no_second_install_without_explicit_disposal(pg):
    psycopg = pytest.importorskip("psycopg")
    compiler.install_pg_v18_lab(pg)
    with pytest.raises(psycopg.errors.DuplicateSchema):
        compiler.install_pg_v18_lab(pg)
    assert pg.execute("""
        SELECT count(*) FROM pg_tables WHERE schemaname='amora_pg_v18_lab'
    """).fetchone() == (50,)
