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
