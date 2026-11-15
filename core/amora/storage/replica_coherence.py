"""Experimental optimistic coherence fence for a *read-only* replica group.

No environment activation or schema migration. A deployment must first provide
an authoritative monotonic revision updated atomically with every relevant
write, including external writers, and rotate the epoch on restore/replacement.
The fence does not grant authorization or replace a provider-dispatch check.
"""
from __future__ import annotations

from dataclasses import dataclass
import inspect
import sqlite3
import threading
from typing import Callable, Generic, TypeVar

from .replica_read import ReplicaReadBackend

T = TypeVar("T")
STAMP_SQL = "SELECT epoch_id, revision FROM amora_projection_epoch WHERE singleton=1"


class ReplicaCoherenceError(sqlite3.OperationalError):
    pass


@dataclass(frozen=True)
class ProjectionStamp:
    epoch_id: str
    revision: int

    def __post_init__(self):
        if (not isinstance(self.epoch_id, str) or not self.epoch_id
                or type(self.revision) is not int or self.revision < 0):
            raise ReplicaCoherenceError("REPLICA_INVALID_STAMP")


def read_projection_stamp(connection) -> ProjectionStamp:
    rows = connection.execute(STAMP_SQL).fetchall()
    if len(rows) != 1:
        raise ReplicaCoherenceError("REPLICA_STAMP_MISSING")
    return ProjectionStamp(rows[0]["epoch_id"], rows[0]["revision"])


@dataclass(frozen=True)
class VerifiedRead(Generic[T]):
    value: T
    stamp: ProjectionStamp


class VersionedReplicaReadGroup:
    """Serialize refresh/read/check and publish only a completed pure result.

    The caller owns all views exclusively; native auto-sync must be disabled.
    The callback is synchronous, read-only, and must not publish intermediate
    results. An independent primary connection supplies fresh authoritative
    stamps; never use a replica or a long-lived transaction for that callback.
    This is an optimistic read snapshot, not a write transaction or privacy lease.
    """

    def __init__(self, backend: ReplicaReadBackend, primary_stamp: Callable[[], ProjectionStamp]):
        self.backend = backend
        self._primary_stamp = primary_stamp
        self._stamp: ProjectionStamp | None = None
        self._members = ()
        self._lock = threading.RLock()
        self._running = False
        self.backend.invalidate_all()

    def invalidate(self):
        with self._lock:
            self._stamp = None
            self._members = ()
            self.backend.invalidate_all()

    def _primary(self):
        stamp = self._primary_stamp()
        if not isinstance(stamp, ProjectionStamp):
            raise ReplicaCoherenceError("REPLICA_INVALID_PRIMARY_STAMP")
        return stamp

    def read(self, operation: Callable[[], T]) -> T:
        return self.read_verified(operation).value

    def read_verified(self, operation: Callable[[], T]) -> VerifiedRead[T]:
        with self._lock:
            if self._running:
                raise ReplicaCoherenceError("REPLICA_NESTED_READ")
            self._running = True
            try:
                members = tuple(self.backend.connections)
                if not members:
                    raise ReplicaCoherenceError("REPLICA_NO_VIEWS")
                start = self._primary()
                if self._stamp == start and self._members == members:
                    self.backend._resume_all()
                else:
                    self.backend.sync_all()
                if any(read_projection_stamp(view) != start for view in members):
                    raise ReplicaCoherenceError("REPLICA_REVISION_MISMATCH")
                result = operation()
                if inspect.isawaitable(result):
                    if inspect.iscoroutine(result):
                        result.close()
                    raise ReplicaCoherenceError("REPLICA_ASYNC_CALLBACK")
                if tuple(self.backend.connections) != members or self._primary() != start:
                    raise ReplicaCoherenceError("REPLICA_CHANGED_DURING_READ")
                # Check the local versions once more before releasing the value.
                if any(read_projection_stamp(view) != start for view in members):
                    raise ReplicaCoherenceError("REPLICA_CHANGED_DURING_READ")
                self._stamp, self._members = start, members
                self.backend._suspend_all()
                return VerifiedRead(result, start)
            except BaseException:
                self.invalidate()
                raise
            finally:
                self._running = False
