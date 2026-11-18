"""Reviewed PostgreSQL v18 mappings for AvatarStore and SQLBlobStore.

File-backed avatar assets remain governed by their existing file-volume
contract. SQLBlobStore uses BYTEA binding (no SQLite blob conversion).
"""
from __future__ import annotations
from .postgres_runtime_connection import ReviewedStatement


def avatar_statements() -> dict[str,ReviewedStatement]:
    m:dict[str,ReviewedStatement]={}
    def add(src,pg,n,cap=1000):
        key=" ".join(src.split())
        if key in m: raise ValueError("PG_AVATAR_DUPLICATE_SQL")
        m[key]=ReviewedStatement(pg,n,cap)
    add("SELECT project_json FROM avatar_projects WHERE id = ?",
        "SELECT project_json FROM amora_pg_v18_lab.avatar_projects WHERE id=%s",1,1)
    add("""INSERT INTO avatar_projects(id, schema_version, project_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            schema_version=excluded.schema_version,
            project_json=excluded.project_json,
            updated_at=excluded.updated_at""",
        """INSERT INTO amora_pg_v18_lab.avatar_projects(
        id,schema_version,project_json,created_at,updated_at)
        VALUES (%s,%s,%s,%s,%s)
        ON CONFLICT(id) DO UPDATE SET
        schema_version=EXCLUDED.schema_version,
        project_json=EXCLUDED.project_json,
        updated_at=EXCLUDED.updated_at""",5)
    add("SELECT COUNT(*) FROM avatar_projects",
        "SELECT COUNT(*) FROM amora_pg_v18_lab.avatar_projects",0,1)
    add("SELECT file_name, created_at FROM avatar_assets WHERE id = ?",
        "SELECT file_name,created_at FROM amora_pg_v18_lab.avatar_assets WHERE id=%s FOR UPDATE",1,1)
    add("""INSERT INTO avatar_assets(id, content_type, file_name, size_bytes, sha256, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            content_type=excluded.content_type,
            file_name=excluded.file_name,
            size_bytes=excluded.size_bytes,
            sha256=excluded.sha256,
            updated_at=excluded.updated_at""",
        """INSERT INTO amora_pg_v18_lab.avatar_assets(
        id,content_type,file_name,size_bytes,sha256,created_at,updated_at)
        VALUES (%s,%s,%s,%s,%s,%s,%s)
        ON CONFLICT(id) DO UPDATE SET
        content_type=EXCLUDED.content_type,
        file_name=EXCLUDED.file_name,
        size_bytes=EXCLUDED.size_bytes,
        sha256=EXCLUDED.sha256,
        updated_at=EXCLUDED.updated_at""",7)
    add("SELECT * FROM avatar_assets WHERE id = ?",
        "SELECT * FROM amora_pg_v18_lab.avatar_assets WHERE id=%s",1,1)
    add("SELECT * FROM avatar_assets ORDER BY updated_at DESC",
        "SELECT * FROM amora_pg_v18_lab.avatar_assets ORDER BY updated_at DESC",0,10000)
    add("DELETE FROM avatar_assets WHERE id = ?",
        "DELETE FROM amora_pg_v18_lab.avatar_assets WHERE id=%s",1)
    add("SELECT COUNT(*) FROM avatar_assets",
        "SELECT COUNT(*) FROM amora_pg_v18_lab.avatar_assets",0,1)
    add("INSERT INTO avatar_asset_blobs(file_name,body) VALUES (?,?) ON CONFLICT(file_name) DO UPDATE SET body=excluded.body",
        """INSERT INTO amora_pg_v18_lab.avatar_asset_blobs(file_name,body)
        VALUES (%s,%s) ON CONFLICT(file_name) DO UPDATE SET
        body=EXCLUDED.body""",2)
    add("SELECT body FROM avatar_asset_blobs WHERE file_name=?",
        "SELECT body FROM amora_pg_v18_lab.avatar_asset_blobs WHERE file_name=%s",1,1)
    add("DELETE FROM avatar_asset_blobs WHERE file_name=?",
        "DELETE FROM amora_pg_v18_lab.avatar_asset_blobs WHERE file_name=%s",1)
    return m
