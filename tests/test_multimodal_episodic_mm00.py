"""MM.00D.2 — selected multimodal evidence, existing memory and replay."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID

import pytest
from pydantic import ValidationError

from amora.events import PrivacyScope
from amora.events.multimodal import (
    EvidenceKind, ObservationSource, SensoryModality, UnknownReason, build_observation_event,
)
from amora.identity.models import Entity, EntityType, build_initial_registry
from amora.memory import MemoryKind, MemoryQuery
from amora.memory.episodic_observations import EpisodeSelection, EpisodicObservationConsolidator
from amora.memory.models import ProvenanceKind
from amora.memory.service import MemoryService
from amora.memory.store import MemoryStore
from amora.storage import AmoraDatabase
from amora.time import SimulatedClock
from remote_storage_contracts_v13_source import remote
from test_multimodal_contract_mm00 import NOW, context, observation


def registry():
    result = build_initial_registry()
    result.add_entity(Entity(id="PERSON_TEST", canonical_name="Pessoa teste", entity_type=EntityType.PERSON))
    return result


def open_stores(path, backend=None):
    db = AmoraDatabase(path, backend=backend)
    db.migrate()
    identities = registry()
    db.save_registry(identities)
    clock = SimulatedClock(NOW)
    memory = MemoryService(MemoryStore(path, clock, identities, backend=backend), clock)
    return db, memory


def selected(*ids, **overrides):
    return EpisodeSelection(**{
        "evidence_ids": ids, "recipient_id": "PERSON_BK",
        "importance": .9, "novelty": .9, "goal_relevance": .9,
        **overrides,
    })


def pair(db, *, screen_scope=PrivacyScope.PERSONAL, screen_remote=False, screen_owner="PERSON_BK",
         screen_audience=(), screen_session="session-1", screen_correlation=UUID(int=3),
         screen_start=NOW + timedelta(seconds=3), screen_kind=EvidenceKind.OBSERVED):
    speech = build_observation_event(
        context(remote_processing_allowed=True, audience=("PERSON_TEST",)),
        observation(text="A pata do coelho branco está travando perto do rio."),
    )
    screen = build_observation_event(
        context(source=ObservationSource.SCREEN, stream_id=UUID(int=77), actor_id=None,
                owner_entity_id=screen_owner, audience=screen_audience,
                privacy_scope=screen_scope, remote_processing_allowed=screen_remote,
                session_id=screen_session, correlation_id=screen_correlation),
        observation(modality=SensoryModality.SCREEN, evidence_kind=screen_kind,
                    capture_started_at=screen_start,
                    capture_ended_at=screen_start + timedelta(seconds=1),
                    received_at=screen_start + timedelta(seconds=2),
                    text="Coelho branco corre entre árvores na margem do rio.",
                    provider="vision", model="test-vision"),
    )
    assert db.append_event(speech)
    assert db.append_event(screen)
    return speech, screen


@pytest.mark.parametrize("mode", ["sqlite", "hrana"])
def test_multimodal_episode_recall_after_restart_and_exact_replay(remote, tmp_path, mode):
    backend, server = remote
    backend = backend if mode == "hrana" else None
    path = tmp_path / "episode.db"
    db, memory = open_stores(path, backend)
    try:
        speech, screen = pair(db, screen_scope=PrivacyScope.PRIVATE)
        engine = EpisodicObservationConsolidator(db, memory)
        sel = selected(speech.id, screen.id)
        record_id, candidate = engine.build_candidate(sel)
        assert candidate.provenance.kind == ProvenanceKind.DERIVED
        assert candidate.provenance.source_event_id == speech.id
        assert not candidate.provenance.derived_from
        assert candidate.privacy_scope == PrivacyScope.PRIVATE
        assert candidate.audience == {"PERSON_BK"}
        assert not candidate.remote_processing_allowed
        assert "relatado" in candidate.content and "observado" in candidate.content
        assert "pata do coelho branco" in candidate.content
        assert "entre árvores na margem do rio" in candidate.content
        assert engine.build_candidate(selected(screen.id, speech.id)) == (record_id, candidate)
        result = engine.consolidate(sel)
        assert result.memory is not None and result.memory.id == record_id
        assert result.memory.kind == MemoryKind.EPISODIC
        assert memory.store.count() == 1
        assert db.event_count() == 2
    finally:
        memory.close()
        db.close()

    db, memory = open_stores(path, backend)
    try:
        engine = EpisodicObservationConsolidator(db, memory)
        hits = memory.recall(MemoryQuery(text="coelho branco rio", projection_recipient_id="PERSON_BK"))
        assert hits and hits[0].memory.id == record_id
        assert "pata do coelho branco" in hits[0].memory.content
        assert not memory.recall(MemoryQuery(text="coelho", projection_recipient_id="PERSON_TEST"))
        assert not memory.recall(MemoryQuery(text="coelho", projection_recipient_id="PERSON_BK",
                                             projection_remote_only=True))
        retry = engine.consolidate(selected(screen.id, speech.id))
        assert retry.memory is None and retry.decision.reason_codes == ["MEMORY_CREATE_ALREADY_EXISTS"]
        assert memory.store.count() == 1
        memory.archive(record_id)
        assert engine.consolidate(sel).memory is None
        assert memory.store.count() == 0
        assert memory.store.count(include_archived=True) == 1
        assert memory.get(record_id).archived_at is not None
    finally:
        memory.close()
        db.close()
    if mode == "hrana":
        assert not server.streams


@pytest.mark.parametrize("override,reason", [
    ({"screen_owner": "PERSON_TEST"}, "EPISODE_EVIDENCE_FORBIDDEN"),
    ({"screen_session": "other"}, "EPISODE_SESSION_MISMATCH"),
    ({"screen_correlation": UUID(int=9)}, "EPISODE_SESSION_MISMATCH"),
    ({"screen_start": NOW + timedelta(seconds=121)}, "EPISODE_WINDOW_EXCEEDED"),
    ({"screen_audience": ("PERSON_UNKNOWN",)}, "EPISODE_IDENTITY_UNKNOWN"),
])
def test_selection_rejects_mismatched_or_unauthorized_evidence(tmp_path, override, reason):
    db, memory = open_stores(tmp_path / "episode.db")
    try:
        speech, screen = pair(db, **override)
        engine = EpisodicObservationConsolidator(db, memory)
        with pytest.raises(ValueError, match=reason):
            engine.consolidate(selected(speech.id, screen.id))
        assert memory.store.count() == 0
    finally:
        memory.close()
        db.close()


def test_unknown_missing_duplicate_and_low_signal(tmp_path):
    db, memory = open_stores(tmp_path / "episode.db")
    try:
        speech, screen = pair(db)
        engine = EpisodicObservationConsolidator(db, memory)
        with pytest.raises(ValueError, match="EPISODE_RECIPIENT_UNKNOWN"):
            engine.consolidate(selected(speech.id, recipient_id="PERSON_UNKNOWN"))
        with pytest.raises(ValueError, match="EPISODE_EVIDENCE_UNAVAILABLE"):
            engine.consolidate(selected(UUID(int=999)))
        with pytest.raises(ValueError, match="EPISODE_DUPLICATE_EVIDENCE"):
            engine.consolidate(selected(speech.id, speech.id))
        with pytest.raises(ValidationError):
            selected()
        with pytest.raises(ValidationError):
            selected(speech.id, importance=2)
        gap = build_observation_event(context(), observation(
            sequence=99, evidence_kind=EvidenceKind.UNKNOWN, text=None, unknown_reason=UnknownReason.GAP,
        ))
        assert db.append_event(gap)
        with pytest.raises(ValueError, match="EPISODE_UNKNOWN_EVIDENCE"):
            engine.consolidate(selected(gap.id))
        result = engine.consolidate(EpisodeSelection(evidence_ids=(speech.id, screen.id), recipient_id="PERSON_BK"))
        assert result.memory is None
        assert "WRITE_GATE_REJECT_LOW_SIGNAL" in result.decision.reason_codes
        assert memory.store.count() == 0
    finally:
        memory.close()
        db.close()


def test_inference_is_labeled_and_not_promoted_to_fact(tmp_path):
    db, memory = open_stores(tmp_path / "episode.db")
    try:
        speech, screen = pair(db, screen_kind=EvidenceKind.INFERRED)
        result = EpisodicObservationConsolidator(db, memory).consolidate(selected(screen.id, speech.id))
        assert result.memory.kind == MemoryKind.EPISODIC
        assert "inferido (não confirmado)" in result.memory.content
        assert result.memory.provenance.kind == ProvenanceKind.DERIVED
    finally:
        memory.close()
        db.close()


def test_atomic_rollback_and_replay(tmp_path):
    db, memory = open_stores(tmp_path / "episode.db")
    try:
        speech, screen = pair(db)
        engine = EpisodicObservationConsolidator(db, memory)
        sel = selected(speech.id, screen.id)
        record_id, _ = engine.build_candidate(sel)
        with db._conn:
            db._conn.execute("CREATE TRIGGER fail_episode_tag BEFORE INSERT ON memory_tags "
                             "BEGIN SELECT RAISE(ABORT, 'test rollback'); END")
        import sqlite3
        with pytest.raises(sqlite3.DatabaseError):
            engine.consolidate(sel)
        assert memory.get(record_id) is None
        assert memory.store.count() == 0
        with db._conn:
            db._conn.execute("DROP TRIGGER fail_episode_tag")
        assert engine.consolidate(sel).memory.id == record_id
        assert memory.store.count() == 1
    finally:
        memory.close()
        db.close()


def test_two_independent_writers_cannot_overwrite_episode(tmp_path):
    path = tmp_path / "episode.db"
    db, memory = open_stores(path)
    try:
        speech, screen = pair(db)
        engine = EpisodicObservationConsolidator(db, memory)
        record_id, candidate = engine.build_candidate(selected(speech.id, screen.id))
        barrier = Barrier(2)
        def writer():
            other = MemoryService(MemoryStore(path, SimulatedClock(NOW), registry()), SimulatedClock(NOW))
            try:
                barrier.wait(timeout=10)
                return other.remember(candidate, record_id=record_id, insert_only=True)
            finally:
                other.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(writer) for _ in range(2)]
            results = [future.result(timeout=20) for future in futures]
        assert sum(result.memory is not None for result in results) == 1
        assert memory.store.count() == 1
        assert memory.get(record_id).content == candidate.content
        # A replay cannot replace content or reset indexes/access state.
        altered = candidate.model_copy(update={"content": "conteúdo alterado"})
        result = memory.remember(altered, record_id=record_id, insert_only=True)
        assert result.memory is None
        assert memory.get(record_id).content == candidate.content
        assert not memory.recall(MemoryQuery(text="alterado", require_topic_match=True))
    finally:
        memory.close()
        db.close()


def test_group_intersection_preserves_both_people_and_public_is_not_escalated(tmp_path):
    db, memory = open_stores(tmp_path / "episode.db")
    try:
        speech, screen = pair(db, screen_owner="PERSON_TEST", screen_audience=("PERSON_BK",),
                              screen_remote=True)
        engine = EpisodicObservationConsolidator(db, memory)
        first = engine.build_candidate(selected(speech.id, screen.id))
        second = engine.build_candidate(selected(screen.id, speech.id, recipient_id="PERSON_TEST"))
        assert first == second
        _, candidate = first
        assert candidate.owner_entity_id is None
        assert candidate.audience == {"PERSON_BK", "PERSON_TEST"}
        assert candidate.remote_processing_allowed
        public = build_observation_event(
            context(privacy_scope=PrivacyScope.PUBLIC, owner_entity_id=None, actor_id=None,
                    remote_processing_allowed=True, stream_id=UUID(int=88)),
            observation(modality=SensoryModality.IMAGE, evidence_kind=EvidenceKind.OBSERVED,
                        text="Imagem pública do rio.", provider="vision", model="test"),
        )
        assert db.append_event(public)
        _, combined = engine.build_candidate(selected(public.id, speech.id))
        assert combined.privacy_scope == PrivacyScope.PERSONAL
        assert combined.audience == {"PERSON_BK", "PERSON_TEST"}
    finally:
        memory.close()
        db.close()


def test_content_budget_rejects_instead_of_truncating_evidence(tmp_path):
    db, memory = open_stores(tmp_path / "episode.db")
    try:
        ids = []
        for seq in range(4):
            event = build_observation_event(context(), observation(sequence=seq, text="x" * 4096))
            assert db.append_event(event)
            ids.append(event.id)
        engine = EpisodicObservationConsolidator(db, memory)
        with pytest.raises(ValueError, match="EPISODE_CONTENT_LIMIT"):
            engine.consolidate(selected(*ids))
        assert memory.store.count() == 0
    finally:
        memory.close()
        db.close()
