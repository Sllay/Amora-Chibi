"""MM.00D.3C.1 — bounded selection, no duplicate writing or provider calls."""
from uuid import UUID

import pytest

from amora.events.multimodal import build_observation_event
from amora.memory.episode_selection import EpisodeBatchSelector
from amora.memory.episodic_observations import EpisodeSelection, EpisodicObservationConsolidator
from test_multimodal_contract_mm00 import context, observation
from test_multimodal_episodic_mm00 import open_stores, pair, selected


@pytest.fixture
def setup(tmp_path):
    db, memory = open_stores(tmp_path / "selection.db")
    try:
        yield db, memory, EpisodeBatchSelector(EpisodicObservationConsolidator(db, memory))
    finally:
        memory.close()
        db.close()


def choose(selector, *proposals, **overrides):
    return selector.select(proposals, **{
        "recipient_id": "PERSON_BK", "session_id": "session-1",
        "correlation_id": UUID(int=3), **overrides,
    })


def append(db, sequence, **ctx):
    event = build_observation_event(context(**ctx), observation(sequence=sequence))
    assert db.append_event(event)
    return event.id


def test_gate_ranking_dedupe_overlap_and_no_writes(setup):
    db, memory, selector = setup
    speech, screen = pair(db)
    extra = append(db, 99)
    a = selected(speech.id, screen.id, importance=1., novelty=1.)
    duplicate = selected(screen.id, speech.id, importance=.9, novelty=.9)
    overlap = selected(speech.id, extra, importance=.8, novelty=.8)
    low = EpisodeSelection(evidence_ids=(extra,), recipient_id="PERSON_BK")
    result = choose(selector, duplicate, overlap, low, a)
    assert len(result.selected) == 1
    assert set(result.selected[0].evidence_ids) == {speech.id, screen.id}
    assert result.selected[0].importance == 1.
    assert [item.reason for item in result.outcomes] == [
        "EPISODE_DUPLICATE_PROPOSAL", "EPISODE_OVERLAP_DEFERRED",
        "EPISODE_GATE_REJECTED", "EPISODE_SELECTED",
    ]
    assert choose(selector, a, low, overlap, duplicate).selected == result.selected
    assert memory.store.count() == 0 and db.event_count() == 3


def test_budget_and_deterministic_ties(setup):
    db, memory, selector = setup
    selector = EpisodeBatchSelector(selector.consolidator, max_selected=2)
    proposals = tuple(selected(append(db, n)) for n in range(20, 25))
    result = choose(selector, *proposals)
    assert len(result.selected) == 2
    assert sum(x.reason == "EPISODE_BUDGET_DEFERRED" for x in result.outcomes) == 3
    assert choose(selector, *reversed(proposals)).selected == result.selected
    assert choose(selector).selected == ()
    assert memory.store.count() == 0


def test_scope_recipient_missing_and_invalid_proposals(setup):
    db, memory, selector = setup
    speech, screen = pair(db, screen_owner="PERSON_TEST")
    other_session = append(db, 88, session_id="other")
    valid = selected(speech.id)
    proposals = (
        selected(speech.id, recipient_id="PERSON_TEST"),
        selected(screen.id), selected(other_session),
        selected(UUID(int=999)),
        valid.model_copy(update={"importance": 2}),
        valid, selected(speech.id, speech.id),
    )
    result = choose(selector, *proposals)
    assert result.selected == (valid,)
    assert [x.reason for x in result.outcomes] == [
        "EPISODE_BATCH_RECIPIENT_MISMATCH", "EPISODE_EVIDENCE_FORBIDDEN",
        "EPISODE_BATCH_SCOPE_MISMATCH", "EPISODE_EVIDENCE_UNAVAILABLE",
        "EPISODE_PROPOSAL_INVALID", "EPISODE_SELECTED", "EPISODE_DUPLICATE_EVIDENCE",
    ]
    assert memory.store.count() == 0


def test_limits_and_storage_failure_propagate(setup, monkeypatch):
    db, _, selector = setup
    proposal = selected(append(db, 91))
    with pytest.raises(ValueError, match="BATCH_INVALID"):
        choose(selector, *([proposal] * 9))
    with pytest.raises(ValueError, match="BATCH_LIMIT_INVALID"):
        EpisodeBatchSelector(selector.consolidator, max_proposals=9)
    def fail(_):
        raise RuntimeError("database unavailable")
    monkeypatch.setattr(selector.consolidator.database, "get_observation_event", fail)
    with pytest.raises(RuntimeError, match="database unavailable"):
        choose(selector, proposal)
