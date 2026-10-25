from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import zipfile
from pathlib import Path

from amora.storage.blobs import BlobStore, FileBlobStore, SQLBlobStore
from amora.storage.backend import SQLiteBackend, StorageBackend
from amora.time import Clock

from .models import AvatarAssetInfo, AvatarProject, default_project


_ASSET_ID = re.compile(r"^[A-Za-z0-9_.-]{1,96}$")
_ALLOWED_TYPES = {
    "image/png": ".png",
    "image/webp": ".webp",
    "image/jpeg": ".jpg",
}


class AvatarStore:
    def __init__(self, path: Path, asset_dir: Path, clock: Clock, *, backend: StorageBackend | None = None, blobs: BlobStore | None = None) -> None:
        self.path = path
        self.asset_dir = asset_dir
        self.clock = clock
        self.asset_dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.backend = backend or SQLiteBackend()
        self._conn = self.backend.connect(path)
        self.blobs = blobs or (SQLBlobStore(self._conn) if os.getenv("AMORA_ASSET_STORAGE", "files") == "database" else FileBlobStore(self.asset_dir))

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
        with self._lock, self._conn:
            self.blobs.put(file_name, body)
            old = self._conn.execute("SELECT file_name, created_at FROM avatar_assets WHERE id = ?", (asset_id,)).fetchone()
            created_at = old["created_at"] if old else now.isoformat()
            if old and old["file_name"] != file_name:
                old_path = self.asset_dir / old["file_name"]
                if old_path != path:
                    self.blobs.delete(old["file_name"])
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
        with self._lock:
            body = self.blobs.get(info.file_name)
        if body is None:
            return None
        if len(body) != info.size_bytes or hashlib.sha256(body).hexdigest() != info.sha256:
            raise ValueError("avatar asset integrity mismatch")
        return info, body

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
            self.blobs.delete(info.file_name)
        return True

    def asset_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM avatar_assets").fetchone()
        return int(row[0]) if row else 0

    @staticmethod
    def asset_backup_path(database_backup: Path) -> Path:
        return Path(f"{database_backup}.avatar.zip")

    @staticmethod
    def _snapshot_asset_infos(database_backup: Path) -> list[AvatarAssetInfo]:
        connection = sqlite3.connect(database_backup)
        connection.row_factory = sqlite3.Row
        try:
            rows = connection.execute("SELECT * FROM avatar_assets ORDER BY updated_at DESC").fetchall()
        finally:
            connection.close()
        return [AvatarAssetInfo.model_validate(dict(row)) for row in rows]

    def backup_assets(self, database_backup: Path) -> Path:
        """Create a validated companion archive bound to the SQLite snapshot."""
        destination = self.asset_backup_path(database_backup)
        tmp = destination.with_suffix(destination.suffix + ".tmp")
        infos = self._snapshot_asset_infos(database_backup)
        manifest: list[dict[str, object]] = []
        try:
            with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                for info in infos:
                    if isinstance(self.blobs, SQLBlobStore):
                        with sqlite3.connect(database_backup) as snapshot:
                            row = snapshot.execute("SELECT body FROM avatar_asset_blobs WHERE file_name=?", (info.file_name,)).fetchone()
                        body = bytes(row[0]) if row else None
                    else:
                        body = self.blobs.get(info.file_name)
                    if body is None:
                        raise FileNotFoundError(f"avatar asset missing during backup: {info.id}")
                    digest = hashlib.sha256(body).hexdigest()
                    if digest != info.sha256 or len(body) != info.size_bytes:
                        raise ValueError(f"avatar asset integrity mismatch during backup: {info.id}")
                    archive.writestr(f"assets/{info.file_name}", body)
                    manifest.append({
                        "id": info.id,
                        "file_name": info.file_name,
                        "size_bytes": info.size_bytes,
                        "sha256": info.sha256,
                    })
                archive.writestr(
                    "manifest.json",
                    json.dumps({"version": 1, "assets": manifest}, ensure_ascii=False, separators=(",", ":")),
                )
            tmp.replace(destination)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        return destination

    def restore_assets(self, database_backup: Path) -> int:
        """Restore the companion archive against this store's restored DB metadata."""
        source = self.asset_backup_path(database_backup)
        if not source.is_file():
            if self.asset_count() == 0:
                return 0
            raise FileNotFoundError(source)
        infos = {item.id: item for item in self.list_assets()}
        restored = 0
        with zipfile.ZipFile(source, "r") as archive:
            manifest = json.loads(archive.read("manifest.json"))
            entries = manifest.get("assets", [])
            if not isinstance(entries, list):
                raise ValueError("invalid avatar asset backup manifest")
            entry_ids = {str(item.get("id")) for item in entries if isinstance(item, dict)}
            if entry_ids != set(infos):
                raise ValueError("avatar asset backup manifest does not match database metadata")
            for asset_id, info in infos.items():
                body = archive.read(f"assets/{info.file_name}")
                if len(body) != info.size_bytes or hashlib.sha256(body).hexdigest() != info.sha256:
                    raise ValueError(f"avatar asset backup integrity mismatch: {asset_id}")
                with self._lock, self._conn:
                    self.blobs.put(info.file_name, body)
                restored += 1
        return restored
