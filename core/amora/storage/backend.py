"""Persistence infrastructure, independent of domain services and host names.

Connections expose the small synchronous DB-API surface used by the stores.
A context owns a transaction; remote adapters must preserve read/write atomicity.
No backend may silently switch databases when its destination is unavailable.
"""
from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


@dataclass(frozen=True)
class StorageCapabilities:
    name: str
    local_wal: bool
    consistent_snapshot: bool
    remote: bool = False


class StorageBackend(Protocol):
    capabilities: StorageCapabilities

    def connect(self, path: Path) -> Any: ...
    def backup(self, connection: Any, destination: Path) -> Path: ...


class SQLiteBackend:
    capabilities = StorageCapabilities("sqlite", True, True)

    def connect(self, path: Path) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, check_same_thread=False)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = NORMAL")
        except BaseException:
            connection.close()
            raise
        return connection

    def backup(self, connection: sqlite3.Connection, destination: Path) -> Path:
        destination.parent.mkdir(parents=True, exist_ok=True)
        target = sqlite3.connect(destination)
        try:
            connection.backup(target)
        finally:
            target.close()
        return destination
