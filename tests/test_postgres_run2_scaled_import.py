"""Run 2: deterministic 2,400-row synthetic source import and typed parity.

This deliberately has NO interface accepting private SQLite backups or a
remote PostgreSQL connection. It verifies the existing guarded importer.
"""
from __future__ import annotations

from contextlib import closing
import os
import pytest

from amora.storage.postgres_v18_manifest import synthetic_v18_connection
from amora.storage.postgres_v18_lab_schema import install_pg_v18_lab
from amora.storage.postgres_synthetic_import import import_synthetic_v18

pytestmark=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB")!="1",
    reason="Local synthetic PostgreSQL 18 only",
)

def _raw():
    psycopg=pytest.importorskip("psycopg")
    return psycopg.connect(
        host="127.0.0.1",port=5432,dbname="amora_pg_synthetic",
        user="amora_ci",password="synthetic_only",
        connect_timeout=5,autocommit=True,
    )


def test_pg_exact_typed_2400_memory_row_import_preserves_privacy_and_fts():
    with closing(_raw()) as target,closing(synthetic_v18_connection()) as source:
        target.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            install_pg_v18_lab(target)
            records=[
                (
                    f"fabricated-{i:05d}","semantic",
                    f"Memoria sintetica numero {i} arvore coelho",
                    "observation","synthetic:scaled-source",1.0,
                    ("PRIVATE" if i%3==0 else
                     "PERSONAL" if i%3==1 else "PUBLIC"),
                    "PERSON_BK",1 if i%3!=0 else 0,
                    "2026-11-17T00:00:00+00:00",
                    "2026-11-17T00:00:00+00:00",0.8,0.8,
                    '{"invented":true}',
                )
                for i in range(2400)
            ]
            source.executemany("""
                INSERT INTO memory_records(
                  id,kind,content,provenance_kind,provenance_source,
                  provenance_confidence,privacy_scope,owner_entity_id,
                  remote_processing_allowed,created_at,updated_at,
                  importance,gate_score,metadata_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,records)
            source.executemany("""
                INSERT INTO memory_fts(memory_id,content,tags)
                VALUES (?,?,?)
            """,[(r[0],r[2],"ficticio") for r in records])
            source.executemany(
                "INSERT INTO memory_tags(memory_id,tag) VALUES (?,?)",
                [(r[0],"ficticio") for r in records],
            )
            source.execute(
                "INSERT INTO avatar_asset_blobs(file_name,body) VALUES (?,?)",
                ("invented-binary.png",bytes(range(256))),
            )
            source.commit()
            report=import_synthetic_v18(source,target)
            assert report["typed_row_parity"] is True
            assert report["fts_rows"]==2400
            assert report["relational_rows"]>=4800
            assert target.execute("""
                SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records
                WHERE privacy_scope='PRIVATE' AND remote_processing_allowed=0
            """).fetchone()[0]==800
            assert target.execute("""
                SELECT COUNT(*) FROM amora_pg_v18_lab.memory_records
                WHERE privacy_scope='PUBLIC'
            """).fetchone()[0]==800
            assert target.execute("""
                SELECT COUNT(*) FROM amora_pg_v18_lab.memory_search
            """).fetchone()[0]==2400
            assert target.execute("""
                SELECT body FROM amora_pg_v18_lab.avatar_asset_blobs
                WHERE file_name='invented-binary.png'
            """).fetchone()[0]==bytes(range(256))
            assert source.execute(
                "PRAGMA integrity_check"
            ).fetchone()==("ok",)
        finally:
            target.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
