from __future__ import annotations

import hashlib
import fcntl
import threading
from contextlib import contextmanager
import hmac
import json
import secrets
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

from amora.time import Clock


@dataclass(frozen=True, slots=True)
class PairingCode:
    code: str
    expires_at: datetime


class PairingManager:
    """Local-only, short-lived pairing challenge for trusted clients.

    The clear code is returned only once to the CLI. The cache stores a salted
    hash, expiry and attempt counter. Successful redemption consumes the code.
    """

    def __init__(
        self,
        path: Path,
        clock: Clock,
        *,
        ttl_seconds: int = 300,
        max_attempts: int = 5,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        if max_attempts <= 0:
            raise ValueError("max_attempts must be positive")
        self._thread_lock = threading.RLock()
        self.path = path
        self.clock = clock
        self.ttl_seconds = ttl_seconds
        self.max_attempts = max_attempts
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _exclusive(self):
        with self._thread_lock, self.path.with_suffix(".lock").open("a") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            try:
                yield
            finally:
                fcntl.flock(lock, fcntl.LOCK_UN)

    def issue(self) -> PairingCode:
        with self._exclusive():
            return self._issue()

    def redeem(self, code: str) -> bool:
        with self._exclusive():
            return self._redeem(code)

    def invalidate(self) -> None:
        with self._exclusive():
            self._invalidate()

    def _issue(self) -> PairingCode:
        code = f"{secrets.randbelow(1_000_000):06d}"
        salt = secrets.token_hex(16)
        expires_at = self.clock.now() + timedelta(seconds=self.ttl_seconds)
        self._write(
            {
                "salt": salt,
                "digest": self._digest(salt, code),
                "expires_at": expires_at.isoformat(),
                "attempts": 0,
            }
        )
        return PairingCode(code=code, expires_at=expires_at)

    def _redeem(self, code: str) -> bool:
        record = self._read()
        if record is None:
            return False

        try:
            expires_at = datetime.fromisoformat(str(record["expires_at"]))
            attempts = int(record.get("attempts", 0))
            salt = str(record["salt"])
            expected = str(record["digest"])
        except (KeyError, TypeError, ValueError):
            self._invalidate()
            return False

        if self.clock.now() >= expires_at or attempts >= self.max_attempts:
            self._invalidate()
            return False

        supplied = self._digest(salt, code.strip())
        if hmac.compare_digest(supplied, expected):
            self._invalidate()
            return True

        attempts += 1
        if attempts >= self.max_attempts:
            self._invalidate()
        else:
            record["attempts"] = attempts
            self._write(record)
        return False

    def _invalidate(self) -> None:
        try:
            self.path.unlink()
        except FileNotFoundError:
            pass

    def _read(self) -> dict[str, object] | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None
        except (json.JSONDecodeError, OSError):
            self._invalidate()
            return None

    def _write(self, payload: dict[str, object]) -> None:
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(self.path)

    @staticmethod
    def _digest(salt: str, code: str) -> str:
        return hashlib.sha256(f"{salt}:{code}".encode("utf-8")).hexdigest()
