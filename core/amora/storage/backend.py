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


class AtomicSQLiteConnection(sqlite3.Connection):
    def __enter__(self):
        if not self.in_transaction:
            self.execute("BEGIN IMMEDIATE")
        return self


class SQLiteBackend:
    capabilities = StorageCapabilities("sqlite", True, True)

    def connect(self, path: Path) -> sqlite3.Connection:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, check_same_thread=False, factory=AtomicSQLiteConnection)
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


def backend_from_environment():
    import os
    choice = os.getenv("AMORA_STORAGE_BACKEND", "sqlite")
    if choice == "sqlite":
        return SQLiteBackend()
    if choice == "libsql":
        from .remote import LibSQLBackend
        return LibSQLBackend(os.getenv("AMORA_LIBSQL_URL", ""), os.getenv("AMORA_SECRET_LIBSQL_AUTH_TOKEN", ""))
    raise ValueError("AMORA_STORAGE_BACKEND must be sqlite or libsql")


def is_duplicate_error(exc: sqlite3.IntegrityError) -> bool:
    return getattr(exc, "sqlite_errorcode", None) in {sqlite3.SQLITE_CONSTRAINT_UNIQUE, sqlite3.SQLITE_CONSTRAINT_PRIMARYKEY} or str(exc) in {"SQLITE_CONSTRAINT_UNIQUE", "SQLITE_CONSTRAINT_PRIMARYKEY"}
