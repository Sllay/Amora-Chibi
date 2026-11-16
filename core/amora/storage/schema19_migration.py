"""Upgrade the already certified, isolated PCS.4C destination in place.

No source connection, delete, restore or replacement of existing rows.
The caller pins the destination and holds the operator lock.
"""
from contextlib import closing
from pathlib import Path
import tempfile

from .pcs4_recovery import _open, _sha, compare_snapshots, verify_snapshot
from .remote import statements
from .schema19_drill import compare_upgrade
from .sensory_episode_schema import SENSORY_EPISODE_QUEUE_CANDIDATE_SQL


class _TransactionSnapshot:
    """SELECT-only view of the transaction already owned by the caller."""

    def __init__(self, connection):
        self.connection = connection

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, sql, parameters=()):
        if not sql.lstrip().upper().startswith("SELECT "):
            raise ValueError("SCHEMA19_SNAPSHOT_READ_ONLY")
        return self.connection.execute(sql, parameters)


def _verify_pair(original, candidate):
    v18 = verify_snapshot(original, expected_schema=18)
    v19 = verify_snapshot(candidate, expected_schema=19)
    compare_upgrade(original, candidate)
    if v18["archive_sha256"] != v19["archive_sha256"]:
        raise ValueError("SCHEMA19_SOURCE_CHANGED")
    return v18, v19


def _pin_pair(original, candidate, v18, v19):
    for path, report in ((original, v18), (candidate, v19)):
        if (_sha(path) != report["database_sha256"]
                or _sha(Path(str(path) + ".avatar.zip")) != report["archive_sha256"]):
            raise ValueError("SCHEMA19_SOURCE_CHANGED")


def _compare_destination(expected, recovered):
    try:
        return compare_snapshots(expected, recovered)
    except ValueError as exc:
        if str(exc) in {"PCS4C_SCHEMA_MISMATCH", "PCS4C_DATA_MISMATCH"}:
            raise ValueError("SCHEMA19_DESTINATION_DIVERGED") from None
        raise


def migrate_certified_destination(original, candidate, backend):
    """One transaction: exact v18 preflight, additive DDL, exact v19 proof.

    The existing v18 database must match the certified backup. Even a harmless
    drift requires operator review rather than silently discarding differences.
    """
    original, candidate = Path(original), Path(candidate)
    v18, report = _verify_pair(original, candidate)
    with closing(_open(candidate)) as db:
        applied_at = db.execute("SELECT applied_at FROM schema_migrations WHERE version=19").fetchone()[0]
    with tempfile.TemporaryDirectory(prefix="schema19-migrate-") as directory:
        recovered = Path(directory) / "target.db"
        with closing(backend.connect(recovered)) as conn, conn:
            # The remote adapter opens BEGIN IMMEDIATE here. Its backup routine
            # must borrow this transaction, not start a nested one.
            reader = _TransactionSnapshot(conn)
            backend.backup(reader, recovered)
            _compare_destination(original, recovered)
            for sql in statements(SENSORY_EPISODE_QUEUE_CANDIDATE_SQL):
                conn.execute(sql)
            conn.execute("INSERT INTO schema_migrations(version,applied_at) VALUES (?,?)",
                         (19, applied_at))
            backend.backup(reader, recovered)
            report["equal_tables"] = _compare_destination(candidate, recovered)
            _pin_pair(original, candidate, v18, report)
        # No success report until COMMIT returns. An ambiguous commit is
        # reconciled by the read-only review, not a second write.
    return report


def review_certified_destination(original, candidate, backend):
    """Read-only reconciliation; only an exact v18 or exact v19 is accepted."""
    original, candidate = Path(original), Path(candidate)
    v18, v19 = _verify_pair(original, candidate)
    with tempfile.TemporaryDirectory(prefix="schema19-review-") as directory:
        recovered = Path(directory) / "target.db"
        with closing(backend.connect(recovered)) as conn, conn.read_transaction():
            backend.backup(_TransactionSnapshot(conn), recovered)
        try:
            v19["equal_tables"] = compare_snapshots(candidate, recovered)
            outcome, report = "exact", v19
        except ValueError as exc:
            if str(exc) not in {"PCS4C_SCHEMA_MISMATCH", "PCS4C_DATA_MISMATCH"}:
                raise
            v18["equal_tables"] = _compare_destination(original, recovered)
            outcome, report = "v18_exact", v18
        _pin_pair(original, candidate, v18, v19)
    return outcome, report
