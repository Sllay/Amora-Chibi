"""D.3C.2A: isolated durable episode queue; canonical schema is unchanged."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier

import pytest

from amora.events.multimodal import build_observation_event
from amora.memory.episode_work import EnqueueResult, EpisodeWorkRepository, JobOutcome
from amora.memory.episodic_observations import EpisodeSelection, EpisodicObservationConsolidator, episode_record_id
from amora.storage import LATEST_SCHEMA_VERSION
from amora.storage.remote import statements
from amora.storage.sensory_episode_schema import SENSORY_EPISODE_QUEUE_CANDIDATE_SQL
from remote_storage_contracts_v13_source import remote
from test_multimodal_contract_mm00 import context, observation
from test_multimodal_episodic_mm00 import open_stores, pair, selected


def apply_candidate(db):
    with db._lock, db._conn:
        for statement in statements(SENSORY_EPISODE_QUEUE_CANDIDATE_SQL):
            db._conn.execute(statement)


def repository(db, memory, **options):
    return EpisodeWorkRepository(EpisodicObservationConsolidator(db, memory), **options)


def add_event(db, sequence):
    event = build_observation_event(context(), observation(sequence=sequence))
    assert db.append_event(event)
    return event.id


@pytest.mark.parametrize("mode", ["sqlite", "hrana"])
def test_durable_claim_recovery_and_ack_after_restart(remote, tmp_path, mode):
    backend, server = remote
    backend = backend if mode == "hrana" else None
    path = tmp_path / "episode-work.db"
    db, memory = open_stores(path, backend)
    try:
        assert db.schema_version() == LATEST_SCHEMA_VERSION == 18
        assert not db._conn.execute("SELECT 1 FROM sqlite_schema WHERE name='sensory_episode_jobs'").fetchone()
        apply_candidate(db)
        speech, screen = pair(db)
        selection = selected(speech.id, screen.id)
        job_id = episode_record_id(selection.evidence_ids)
        work = repository(db, memory, lease_seconds=10)
        assert work.enqueue(selection) == EnqueueResult.ACCEPTED
        assert work.enqueue(selected(screen.id, speech.id)) == EnqueueResult.DUPLICATE
        first = work.claim()
        assert first.job_id == job_id and first.attempts == 1
        assert set(first.selection.evidence_ids) == {speech.id, screen.id}
        assert work.claim() is None
        row = db._conn.execute("SELECT selection_json FROM sensory_episode_jobs").fetchone()
        assert "coelho" not in row[0]
        assert db._conn.execute("SELECT COUNT(*) FROM sensory_episode_job_evidence").fetchone()[0] == 2
        assert memory.store.count() == 0
        assert db.schema_version() == 18
    finally:
        memory.close()
        db.close()

    db, memory = open_stores(path, backend)
    try:
        work = repository(db, memory, lease_seconds=10)
        memory.clock.advance(timedelta(seconds=10))
        second = work.claim()
        assert second.job_id == job_id and second.attempts == 2
        assert second.lease_token != first.lease_token
        assert not work.finish(first, JobOutcome.REJECTED)
        assert work.finish(second, JobOutcome.REJECTED)
        assert work.state(job_id) == {"status": "done", "attempts": 2, "last_reason": "EPISODE_REJECTED"}
        assert work.claim() is None
        assert work.enqueue(selection) == EnqueueResult.DUPLICATE
        assert memory.store.count() == 0  # repository is not a memory writer
    finally:
        memory.close()
        db.close()
    if mode == "hrana":
        assert not server.streams


def test_capacity_gate_retry_cancel_and_final_expiration(tmp_path):
    db, memory = open_stores(tmp_path / "queue.db")
    try:
        apply_candidate(db)
        a, b, c = (add_event(db, n) for n in (21, 22, 23))
        work = repository(db, memory, max_active=2, max_active_per_session=1,
                          max_attempts=2, lease_seconds=5)
        low = EpisodeSelection(evidence_ids=(a,), recipient_id="PERSON_BK")
        assert work.enqueue(low) == EnqueueResult.LOW_SIGNAL
        assert work.enqueue(selected(a)) == EnqueueResult.ACCEPTED
        assert work.enqueue(selected(b)) == EnqueueResult.CAPACITY
        first = work.claim()
        assert work.finish(first, JobOutcome.TRANSIENT_FAILURE)
        assert work.claim() is None
        memory.clock.advance(timedelta(seconds=15))
        second = work.claim()
        assert second.attempts == 2
        memory.clock.advance(timedelta(seconds=5))
        assert work.claim() is None
        assert work.state(first.job_id)["last_reason"] == "EPISODE_ATTEMPTS_EXHAUSTED"
        assert work.enqueue(selected(b)) == EnqueueResult.ACCEPTED
        claim = work.claim()
        assert work.cancel(claim.job_id)
        assert not work.finish(claim, JobOutcome.REJECTED)
        assert not work.cancel(claim.job_id)
        assert work.state(claim.job_id)["status"] == "cancelled"
        assert work.enqueue(selected(b)) == EnqueueResult.DUPLICATE
        assert work.enqueue(selected(c)) == EnqueueResult.ACCEPTED
        assert work.finish(work.claim(), JobOutcome.REJECTED)
        assert work.claim() is None
        assert memory.store.count() == 0
    finally:
        memory.close()
        db.close()


def test_corrupt_link_and_unauthorized_selection_are_not_claimable(tmp_path):
    db, memory = open_stores(tmp_path / "queue.db")
    try:
        apply_candidate(db)
        speech, screen = pair(db, screen_owner="PERSON_TEST")
        work = repository(db, memory)
        with pytest.raises(ValueError, match="EPISODE_EVIDENCE_FORBIDDEN"):
            work.enqueue(selected(screen.id))
        assert work.enqueue(selected(speech.id)) == EnqueueResult.ACCEPTED
        job_id = episode_record_id((speech.id,))
        with db._conn:
            db._conn.execute("DELETE FROM sensory_episode_job_evidence WHERE job_id=?", (str(job_id),))
        assert work.claim() is None
        assert work.state(job_id)["last_reason"] == "EPISODE_WORK_CORRUPT"
        assert work.enqueue(selected(speech.id)) == EnqueueResult.DUPLICATE
    finally:
        memory.close()
        db.close()


def test_two_independent_claimers_only_one_wins(tmp_path):
    path = tmp_path / "queue.db"
    db, memory = open_stores(path)
    other_db, other_memory = open_stores(path)
    try:
        apply_candidate(db)
        a = add_event(db, 99)
        one, two = repository(db, memory), repository(other_db, other_memory)
        assert one.enqueue(selected(a)) == EnqueueResult.ACCEPTED
        barrier = Barrier(2)
        def claim(work):
            barrier.wait(timeout=10)
            return work.claim()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(claim, work) for work in (one, two)]
            results = [future.result(timeout=20) for future in futures]
        assert sum(result is not None for result in results) == 1
        assert one.state(episode_record_id((a,)))["attempts"] == 1
    finally:
        other_memory.close()
        other_db.close()
        memory.close()
        db.close()
