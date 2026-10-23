from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    home: Path
    data: Path
    backups: Path
    cache: Path
    logs: Path
    secrets: Path
    avatar: Path
    avatar_assets: Path
    database: Path

    @classmethod
    def from_environment(cls) -> "RuntimePaths":
        home = Path(os.environ.get("AMORA_HOME", "~/.amora")).expanduser().resolve()
        avatar = home / "avatar"
        return cls(
            home=home,
            data=home / "data",
            backups=home / "backups",
            cache=home / "cache",
            logs=home / "logs",
            secrets=home / "secrets",
            avatar=avatar,
            avatar_assets=avatar / "assets",
            database=home / "data" / "amora.db",
        )

    def ensure(self) -> None:
        for path in (
            self.home,
            self.data,
            self.backups,
            self.cache,
            self.logs,
            self.secrets,
            self.avatar,
            self.avatar_assets,
        ):
            path.mkdir(parents=True, exist_ok=True)
        try:
            self.home.chmod(0o700)
            self.secrets.chmod(0o700)
            self.avatar.chmod(0o700)
        except OSError:
            pass


@dataclass(frozen=True)
class ServerSettings:
    mode: str
    host: str
    port: int
    durable_filesystem: bool

    @classmethod
    def from_environment(cls) -> "ServerSettings":
        mode = os.getenv("AMORA_MODE", "local").strip().lower()
        if mode not in {"local", "cloud"}:
            raise ValueError("AMORA_MODE must be local or cloud")
        port = int(os.getenv("PORT", os.getenv("AMORA_PORT", "8765")))
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        return cls(mode, os.getenv("AMORA_HOST", "0.0.0.0" if mode == "cloud" else "127.0.0.1"), port,
                   os.getenv("AMORA_DURABLE_FILESYSTEM", "false").lower() == "true")

    def validate_persistence(self, *, remote: bool) -> None:
        asset_backend = os.getenv("AMORA_ASSET_STORAGE", "files")
        if asset_backend not in {"files", "database"}:
            raise ValueError("AMORA_ASSET_STORAGE must be files or database")
        if self.mode != "cloud":
            return
        if not os.getenv("AMORA_SECRET_LOCAL_API_TOKEN", "").strip():
            raise ValueError("cloud requires an injected AMORA_SECRET_LOCAL_API_TOKEN")
        if not remote and not self.durable_filesystem:
            raise ValueError("cloud SQLite requires AMORA_DURABLE_FILESYSTEM=true and a durable volume")
        if remote and not self.durable_filesystem and asset_backend != "database":
            raise ValueError("remote SQL alone does not persist avatar assets; select AMORA_ASSET_STORAGE=database or a durable filesystem")
        if len(os.environ["AMORA_SECRET_LOCAL_API_TOKEN"].encode()) < 32:
            raise ValueError("cloud admin token must contain at least 32 bytes")
