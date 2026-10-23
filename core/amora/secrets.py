from __future__ import annotations

import os
import re
import secrets as secrets_module
from amora.observability import remember_secret
from pathlib import Path

_SECRET_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")


class SecretStore:
    """Small local secret vault.

    Values are intentionally never exposed through repr/list APIs. Environment
    variables override files and are named AMORA_SECRET_<NAME>.
    """

    def __init__(self, directory: Path, *, env_prefix: str = "AMORA_SECRET_") -> None:
        self.directory = directory
        self.env_prefix = env_prefix
        self.directory.mkdir(parents=True, exist_ok=True)
        try:
            self.directory.chmod(0o700)
        except OSError:
            pass

    @staticmethod
    def normalize_name(name: str) -> str:
        normalized = name.strip().upper()
        if not _SECRET_RE.fullmatch(normalized):
            raise ValueError("invalid secret name")
        return normalized

    def _path(self, name: str) -> Path:
        return self.directory / self.normalize_name(name)

    def get(self, name: str) -> str | None:
        normalized = self.normalize_name(name)
        env = os.environ.get(f"{self.env_prefix}{normalized}")
        if env is not None:
            remember_secret(env)
            return env
        path = self._path(normalized)
        if not path.is_file():
            return None
        value = path.read_text(encoding="utf-8")
        remember_secret(value)
        return value

    def set(self, name: str, value: str) -> None:
        if not isinstance(value, str) or not value:
            raise ValueError("secret value must be a non-empty string")
        remember_secret(value)
        path = self._path(name)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(value, encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(path)

    def delete(self, name: str) -> bool:
        path = self._path(name)
        try:
            path.unlink()
        except FileNotFoundError:
            return False
        return True

    def names(self) -> tuple[str, ...]:
        names = {p.name for p in self.directory.iterdir() if p.is_file() and _SECRET_RE.fullmatch(p.name)}
        for key in os.environ:
            if key.startswith(self.env_prefix):
                candidate = key[len(self.env_prefix):]
                if _SECRET_RE.fullmatch(candidate):
                    names.add(candidate)
        return tuple(sorted(names))

    def ensure_token(self, name: str = "LOCAL_API_TOKEN") -> str:
        value = self.get(name)
        if value:
            return value
        value = secrets_module.token_urlsafe(32)
        self.set(name, value)
        return value
