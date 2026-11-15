"""Read-only native replica adapter for isolated PCS certification.

This is NOT selected by backend_from_environment. It has no production
credentials, no write path, no implicit fallback and no freshness claim.
A caller must own the replica and synchronize at an explicit boundary.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Callable

from .backend import StorageCapabilities
from .remote import Result, statements


class ReplicaReadConnection:
    """Adapt native cursor tuples to the existing Hrana row contract.

    SQL is supplied only by audited internal stores, never by users. The
    allowlist is a misuse guard, not a general SQL security sandbox.
    """
    _PRAGMAS = frozenset({"PRAGMA INTEGRITY_CHECK", "PRAGMA FOREIGN_KEY_CHECK",
                          "PRAGMA TABLE_LIST", "PRAGMA QUERY_ONLY"})

    def __init__(self, connection):
        self._connection = connection
        self._ready = False
        self._synced = False
        self._closed = False

    def sync(self):
        if self._closed:
            raise sqlite3.OperationalError("REPLICA_CLOSED")
        self.invalidate()
        self._connection.sync()
        self._synced = True
        self._ready = True

    def invalidate(self):
        self._ready = False
        self._synced = False

    def _suspend(self):
        self._ready = False

    def _resume(self):
        if self._closed or not self._synced:
            raise sqlite3.OperationalError("REPLICA_NOT_SYNCHRONIZED")
        self._ready = True

    def execute(self, sql, parameters=()):
        if self._closed or not self._ready:
            raise sqlite3.OperationalError("REPLICA_NOT_SYNCHRONIZED")
        normalized = sql.strip().rstrip(";").strip().upper()
        if len(list(statements(sql))) != 1 or not (
            normalized.startswith("SELECT ") or normalized.startswith("SELECT\n")
            or normalized in self._PRAGMAS
        ):
            raise sqlite3.OperationalError("REPLICA_READ_ONLY")
        cursor = self._connection.execute(sql, tuple(parameters))
        columns = [entry[0] for entry in cursor.description or ()]
        return Result(columns, cursor.fetchall(), 0)

    def close(self):
        if not self._closed:
            self._closed = True
            self.invalidate()
            self._connection.close()

    def __enter__(self):
        raise sqlite3.OperationalError("REPLICA_READ_ONLY")

    def __exit__(self, *args):
        return False

    def executemany(self, *args):
        raise sqlite3.OperationalError("REPLICA_READ_ONLY")

    def executescript(self, *args):
        raise sqlite3.OperationalError("REPLICA_READ_ONLY")


class ReplicaReadBackend:
    """One separately owned native connection per store; no writes/backups."""
    capabilities = StorageCapabilities("replica-read-lab", False, False)

    def __init__(self, factory: Callable[[Path], object]):
        self.factory = factory
        self.connections: list[ReplicaReadConnection] = []

    def connect(self, path: Path):
        connection = ReplicaReadConnection(self.factory(path))
        self.connections.append(connection)
        return connection

    def sync_all(self):
        # An incomplete refresh must not leave a subset marked ready.
        self.invalidate_all()
        try:
            for connection in self.connections:
                connection.sync()
        except BaseException:
            self.invalidate_all()
            raise

    def invalidate_all(self):
        for connection in self.connections:
            connection.invalidate()

    def _suspend_all(self):
        for connection in self.connections:
            connection._suspend()

    def _resume_all(self):
        try:
            for connection in self.connections:
                connection._resume()
        except BaseException:
            self.invalidate_all()
            raise

    def backup(self, connection, destination):
        raise sqlite3.OperationalError("REPLICA_READ_ONLY")
