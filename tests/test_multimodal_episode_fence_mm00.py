"""D.3C.2B: fenced memory commit, cancellation and durable invalidation."""
import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest

from amora.memory.episode_work import EnqueueResult, JobOutcome
from amora.memory.episodic_observations import EpisodicObservationConsolidator, episode_record_id
from amora.memory import MemoryQuery
from remote_storage_contracts_v13_source import remote
from test_multimodal_episodic_mm00 import open_stores, pair, selected
from test_multimodal_episode_work_mm00 import apply_candidate, repository, add_event


@pytest.mark.parametrize("mode", ["sqlite", "hrana"])
def test_memory_and_ack_commit_together_and_survive_reopen(remote, tmp_path, mode):
    backend, server = remote
    backend = backend if mode == "hrana" else None
    path = tmp_path / "fenced.db"
    db, memory = open_stores(path, backend)
    try:
        apply_candidate(db)
        a, b = pair(db)
        selection = selected(a.id, b.id)
        work = repository(db, memory)
        assert work.enqueue(selection) == EnqueueResult.ACCEPTED
        claim = work.claim()
        with pytest.raises(ValueError, match="SUCCESS_REQUIRES_ATOMIC_WRITE"):
            work.finish(claim, JobOutcome.STORED)
        assert work.state(claim.job_id)["status"] == "claimed"
        result = work.process(claim)
        assert result.memory.id == claim.job_id
        assert work.state(claim.job_id)["last_reason"] == "EPISODE_STORED"
        assert memory.store.count() == 1
        # Pretend the caller lost the successful response before returning.
    finally:
        memory.close()
        db.close()
    db, memory = open_stores(path, backend)
    try:
        work = repository(db, memory)
        assert work.state(claim.job_id)["status"] == "done"
        assert work.claim() is None
        assert work.enqueue(selection) == EnqueueResult.DUPLICATE
        with pytest.raises(RuntimeError, match="WRITE_LEASE_LOST"):
            work.process(claim)
        hits = memory.recall(MemoryQuery(text="coelho rio", projection_recipient_id="PERSON_BK"))
        assert hits and hits[0].memory.id == claim.job_id
        assert memory.store.count() == 1
    finally:
        memory.close()
        db.close()
    if mode == "hrana":
        assert not server.streams


@pytest.mark.parametrize("mode", ["sqlite", "hrana"])
def test_rollback_when_ack_fails_and_retry(remote, tmp_path, mode):
    backend, server = remote
    backend = backend if mode == "hrana" else None
    db, memory = open_stores(tmp_path / "rollback.db", backend)
    try:
        apply_candidate(db)
        a, b = pair(db)
        work = repository(db, memory)
        assert work.enqueue(selected(a.id, b.id)) == EnqueueResult.ACCEPTED
        claim = work.claim()
        with db._conn:
            db._conn.execute("CREATE TRIGGER fail_episode_ack BEFORE UPDATE ON sensory_episode_jobs "
                             "WHEN NEW.status='done' BEGIN SELECT RAISE(ABORT,'test ack rollback'); END")
        with pytest.raises(sqlite3.DatabaseError):
            work.process(claim)
        assert memory.get(claim.job_id) is None
        assert work.state(claim.job_id)["status"] == "claimed"
        assert db._conn.execute("SELECT COUNT(*) FROM memory_tags WHERE memory_id=?",
                                (str(claim.job_id),)).fetchone()[0] == 0
        with db._conn:
            db._conn.execute("DROP TRIGGER fail_episode_ack")
        assert work.process(claim).memory.id == claim.job_id
        assert work.state(claim.job_id)["status"] == "done"
    finally:
        memory.close()
        db.close()
    if mode == "hrana":
        assert not server.streams


def test_expired_reclaimed_cancelled_and_forged_claim_fail_closed(tmp_path):
    db, memory = open_stores(tmp_path / "lease.db")
    try:
        apply_candidate(db)
        a = add_event(db, 31)
        work = repository(db, memory, lease_seconds=5)
        assert work.enqueue(selected(a)) == EnqueueResult.ACCEPTED
        first = work.claim()
        memory.clock.advance(timedelta(seconds=5))
        with pytest.raises(RuntimeError, match="WRITE_LEASE_LOST"):
            work.process(first)
        second = work.claim()
        with pytest.raises(RuntimeError, match="WRITE_LEASE_LOST"):
            work.process(first)
        forged = replace(second, selection=second.selection.model_copy(update={"importance": 1.}))
        with pytest.raises(RuntimeError, match="WRITE_LEASE_LOST"):
            work.process(forged)
        assert work.cancel(second.job_id)
        with pytest.raises(RuntimeError, match="WRITE_LEASE_LOST"):
            work.process(second)
        assert memory.store.count() == 0
        with pytest.raises(RuntimeError, match="WRITE_LEASE_REQUIRED"):
            EpisodicObservationConsolidator(db, memory).consolidate(selected(a))
    finally:
        memory.close()
        db.close()


@pytest.mark.parametrize("mode", ["sqlite", "hrana"])
def test_invalidation_before_and_after_write_blocks_future_replay(remote, tmp_path, mode):
    backend, server = remote
    backend = backend if mode == "hrana" else None
    db, memory = open_stores(tmp_path / "invalidate.db", backend)
    try:
        apply_candidate(db)
        a, b = pair(db)
        c = add_event(db, 32)
        work = repository(db, memory)
        assert work.enqueue(selected(a.id, b.id)) == EnqueueResult.ACCEPTED
        first = work.claim()
        assert work.invalidate_evidence(a.id) == 0
        assert work.state(first.job_id)["status"] == "cancelled"
        with pytest.raises(RuntimeError, match="WRITE_LEASE_LOST"):
            work.process(first)
        with pytest.raises(ValueError, match="EVIDENCE_INVALIDATED"):
            work.enqueue(selected(a.id, c))
        assert work.enqueue(selected(b.id, c)) == EnqueueResult.ACCEPTED
        second = work.claim()
        assert work.process(second).memory.id == second.job_id
        assert work.invalidate_evidence(c) == 1
        assert work.invalidate_evidence(c) == 0
        assert memory.get(second.job_id).archived_at is not None
        assert work.state(second.job_id)["status"] == "cancelled"
        assert not memory.recall(MemoryQuery(text="coelho", projection_recipient_id="PERSON_BK"))
        with pytest.raises(ValueError, match="EVIDENCE_INVALIDATED"):
            work.enqueue(selected(c))
        assert db.event_count() == 3
        assert db._conn.execute("SELECT COUNT(*) FROM sensory_episode_invalidations").fetchone()[0] == 2
    finally:
        memory.close()
        db.close()
    if mode == "hrana":
        assert not server.streams


def test_existing_episode_is_acknowledged_without_overwriting_or_reviving(tmp_path):
    db, memory = open_stores(tmp_path / "existing.db")
    try:
        a = add_event(db, 41)
        sel = selected(a)
        original = EpisodicObservationConsolidator(db, memory).consolidate(sel).memory
        assert original.id == episode_record_id((a,))
        memory.archive(original.id)
        apply_candidate(db)
        work = repository(db, memory)
        assert work.enqueue(sel) == EnqueueResult.ACCEPTED
        assert work.process(work.claim()).memory is None
        assert work.state(original.id)["last_reason"] == "EPISODE_DUPLICATE"
        assert memory.get(original.id).archived_at is not None
        assert memory.store.count(include_archived=True) == 1
    finally:
        memory.close()
        db.close()
