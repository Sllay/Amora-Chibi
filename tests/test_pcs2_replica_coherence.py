"""Unit contracts for the optimistic local read fence (no network or SDK)."""
import sqlite3
from pathlib import Path

import pytest

from amora.storage.replica_read import ReplicaReadBackend
from amora.storage.replica_coherence import (
    ProjectionStamp, ReplicaCoherenceError, VersionedReplicaReadGroup,
)


class Native:
    def __init__(self, primary):
        self.primary = primary
        self.syncs = 0
        self.fail = False
        self.conn = sqlite3.connect(":memory:")
        self.conn.executescript("""
            CREATE TABLE amora_projection_epoch(singleton INTEGER, epoch_id TEXT, revision INTEGER);
            INSERT INTO amora_projection_epoch VALUES(1, 'fixture', 0);
            CREATE TABLE records(body TEXT);
            INSERT INTO records VALUES('initial');
        """)

    def sync(self):
        self.syncs += 1
        if self.fail:
            raise RuntimeError("synthetic refresh failure")
        stamp, body = self.primary
        self.conn.execute("UPDATE amora_projection_epoch SET epoch_id=?, revision=?",
                          (stamp.epoch_id, stamp.revision))
        self.conn.execute("UPDATE records SET body=?", (body,))

    def execute(self, *args):
        return self.conn.execute(*args)

    def close(self):
        self.conn.close()


@pytest.fixture
def lab():
    primary = [ProjectionStamp("fixture", 1), "first"]
    natives = []
    def factory(_):
        native = Native(primary)
        natives.append(native)
        return native
    backend = ReplicaReadBackend(factory)
    first, second = backend.connect(Path("one")), backend.connect(Path("two"))
    group = VersionedReplicaReadGroup(backend, lambda: primary[0])
    try:
        yield primary, natives, backend, group, first, second
    finally:
        for view in backend.connections:
            view.close()


def text(view):
    return view.execute("SELECT body FROM records").fetchone()[0]


def test_pcs2d_hot_read_reuses_snapshot_and_seals_views(lab):
    primary, natives, _, group, first, second = lab
    assert group.read(lambda: (text(first), text(second))) == ("first", "first")
    assert group.read(lambda: text(second)) == "first"
    assert [n.syncs for n in natives] == [1, 1]
    with pytest.raises(sqlite3.OperationalError, match="NOT_SYNCHRONIZED"):
        text(first)
    primary[:] = [ProjectionStamp("fixture", 2), "updated"]
    assert group.read(lambda: (text(first), text(second))) == ("updated", "updated")
    assert [n.syncs for n in natives] == [2, 2]
    # A restore with the same numeric revision must be a new epoch.
    primary[:] = [ProjectionStamp("restored", 2), "restored"]
    assert group.read(lambda: text(first)) == "restored"
    assert [n.syncs for n in natives] == [3, 3]


def test_pcs2d_change_during_read_discards_result_and_refreshes(lab):
    primary, natives, _, group, first, second = lab
    def race():
        value = text(first)
        primary[:] = [ProjectionStamp("fixture", 2), "revoked"]
        return value
    with pytest.raises(ReplicaCoherenceError, match="CHANGED_DURING_READ"):
        group.read(race)
    for view in (first, second):
        with pytest.raises(sqlite3.OperationalError, match="NOT_SYNCHRONIZED"):
            text(view)
    assert group.read(lambda: text(first)) == "revoked"
    assert [n.syncs for n in natives] == [2, 2]


def test_pcs2d_failed_sync_and_primary_failure_fail_closed(lab):
    primary, natives, _, group, first, second = lab
    assert group.read(lambda: text(first)) == "first"
    primary[:] = [ProjectionStamp("fixture", 2), "updated"]
    natives[1].fail = True
    with pytest.raises(RuntimeError, match="synthetic refresh failure"):
        group.read(lambda: text(first))
    for view in (first, second):
        with pytest.raises(sqlite3.OperationalError, match="NOT_SYNCHRONIZED"):
            text(view)
    natives[1].fail = False
    assert group.read(lambda: text(first)) == "updated"
    def unavailable():
        raise OSError("primary unavailable")
    group._primary_stamp = unavailable
    with pytest.raises(OSError, match="primary unavailable"):
        group.read(lambda: text(first))
    with pytest.raises(sqlite3.OperationalError, match="NOT_SYNCHRONIZED"):
        text(first)


def test_pcs2d_mismatched_local_revision_and_new_member(lab):
    primary, natives, backend, group, first, second = lab
    assert group.read(lambda: text(first)) == "first"
    # Independent local corruption/unsynced view must not return its data.
    natives[1].conn.execute("UPDATE amora_projection_epoch SET revision=0")
    with pytest.raises(ReplicaCoherenceError, match="REVISION_MISMATCH"):
        group.read(lambda: text(second))
    third = backend.connect(Path("three"))
    assert group.read(lambda: (text(first), text(third))) == ("first", "first")
    assert natives[2].syncs == 1


def test_pcs2d_rejects_nested_and_async_callbacks(lab):
    _, _, _, group, first, _ = lab
    with pytest.raises(ReplicaCoherenceError, match="NESTED_READ"):
        group.read(lambda: group.read(lambda: text(first)))
    async def asynchronous():
        return "must not escape"
    with pytest.raises(ReplicaCoherenceError, match="ASYNC_CALLBACK"):
        group.read(asynchronous)
    assert group.read(lambda: text(first)) == "first"
