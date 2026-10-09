from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import threading
from pathlib import Path

from amora.time import Clock

from .models import AvatarAssetInfo, AvatarProject, default_project


_ASSET_ID = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
_ALLOWED_TYPES = {
    "image/png": ".png",
    "image/webp": ".webp",
    "image/jpeg": ".jpg",
}


class AvatarStore:
    def __init__(self, path: Path, asset_dir: Path, clock: Clock) -> None:
        self.path = path
        self.asset_dir = asset_dir
        self.clock = clock
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.execute("PRAGMA foreign_keys = ON")
            self._conn.execute("PRAGMA journal_mode = WAL")
            self._conn.execute("PRAGMA synchronous = NORMAL")

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def get_project(self, project_id: str = "default") -> AvatarProject:
        with self._lock:
            row = self._conn.execute(
                "SELECT project_json FROM avatar_projects WHERE id = ?",
                (project_id,),
            ).fetchone()
        if row is None:
            return default_project()
        return AvatarProject.model_validate(json.loads(row["project_json"]))

    def put_project(self, project: AvatarProject) -> AvatarProject:
        payload = json.dumps(project.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        now = self.clock.now().isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                """
                INSERT INTO avatar_projects(id, schema_version, project_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    schema_version=excluded.schema_version,
                    project_json=excluded.project_json,
                    updated_at=excluded.updated_at
                """,
                (project.id, project.schema_version, payload, now, now),
            )
        return project

    def project_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM avatar_projects").fetchone()
        return int(row[0]) if row else 0

    def put_asset(self, asset_id: str, content_type: str, body: bytes) -> AvatarAssetInfo:
        if not _ASSET_ID.fullmatch(asset_id):
            raise ValueError("invalid asset id")
        normalized_type = content_type.split(";", 1)[0].strip().lower()
        suffix = _ALLOWED_TYPES.get(normalized_type)
        if suffix is None:
            raise ValueError("unsupported avatar asset content type")
        if not body:
            raise ValueError("empty avatar asset")
        file_name = f"{asset_id}{suffix}"
        path = self.asset_dir / file_name
        digest = hashlib.sha256(body).hexdigest()
        now = self.clock.now()
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_bytes(body)
        tmp.replace(path)
        with self._lock, self._conn:
            old = self._conn.execute("SELECT file_name, created_at FROM avatar_assets WHERE id = ?", (asset_id,)).fetchone()
            created_at = old["created_at"] if old else now.isoformat()
            if old and old["file_name"] != file_name:
                old_path = self.asset_dir / old["file_name"]
                if old_path != path:
                    old_path.unlink(missing_ok=True)
            self._conn.execute(
                """
                INSERT INTO avatar_assets(id, content_type, file_name, size_bytes, sha256, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    content_type=excluded.content_type,
                    file_name=excluded.file_name,
                    size_bytes=excluded.size_bytes,
                    sha256=excluded.sha256,
                    updated_at=excluded.updated_at
                """,
                (asset_id, normalized_type, file_name, len(body), digest, created_at, now.isoformat()),
            )
        return self.get_asset_info(asset_id)  # type: ignore[return-value]

    def get_asset_info(self, asset_id: str) -> AvatarAssetInfo | None:
        with self._lock:
            row = self._conn.execute("SELECT * FROM avatar_assets WHERE id = ?", (asset_id,)).fetchone()
        if row is None:
            return None
        return AvatarAssetInfo.model_validate(dict(row))

    def read_asset(self, asset_id: str) -> tuple[AvatarAssetInfo, bytes] | None:
        info = self.get_asset_info(asset_id)
        if info is None:
            return None
        path = self.asset_dir / info.file_name
        if not path.is_file():
            return None
        return info, path.read_bytes()

    def list_assets(self) -> list[AvatarAssetInfo]:
        with self._lock:
            rows = self._conn.execute("SELECT * FROM avatar_assets ORDER BY updated_at DESC").fetchall()
        return [AvatarAssetInfo.model_validate(dict(row)) for row in rows]

    def delete_asset(self, asset_id: str) -> bool:
        info = self.get_asset_info(asset_id)
        if info is None:
            return False
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM avatar_assets WHERE id = ?", (asset_id,))
        (self.asset_dir / info.file_name).unlink(missing_ok=True)
        return True

    def asset_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM avatar_assets").fetchone()
        return int(row[0]) if row else 0
