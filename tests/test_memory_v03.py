from datetime import datetime, timezone
from pathlib import Path

from amora.events import PrivacyScope
from amora.identity import build_initial_registry
from amora.memory import (
    MemoryCandidate,
    MemoryKind,
    MemoryProvenance,
    MemoryQuery,
    MemoryService,
    MemoryStore,
    ProvenanceKind,
)
from amora.storage import AmoraDatabase
from amora.time import SimulatedClock


def build_memory(tmp_path: Path):
    clock = SimulatedClock(datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc))
    db = AmoraDatabase(tmp_path / "amora.db")
    db.migrate()
    registry = build_initial_registry()
    db.save_registry(registry)
    store = MemoryStore(db.path, clock, registry)
    return clock, db, MemoryService(store, clock)


def important_candidate(**overrides):
    values = dict(
        kind=MemoryKind.EPISODIC,
        content="BK mostrou para Amora um desenho novo do avatar chibi.",
        provenance=MemoryProvenance(
            kind=ProvenanceKind.OBSERVATION,
            source="test",
            actor_id="PERSON_BK",
            confidence=0.98,
        ),
        entity_refs={"PERSON_BK": 1.0, "ENTITY_AMORA": 1.0},
        tags={"avatar", "desenho"},
        importance=0.8,
        novelty=0.9,
        emotional_weight=0.4,
        relationship_relevance=0.8,
        future_consequence=0.5,
        rarity=0.5,
        goal_relevance=0.7,
    )
    values.update(overrides)
    return MemoryCandidate(**values)


def test_write_gate_rejects_banal_low_signal_event(tmp_path: Path) -> None:
    _, db, memory = build_memory(tmp_path)
    try:
        candidate = MemoryCandidate(
            kind=MemoryKind.EPISODIC,
            content="Um ruído banal ocorreu.",
            provenance=MemoryProvenance(kind=ProvenanceKind.OBSERVATION, source="test"),
            importance=0.05,
            novelty=0.05,
        )
        result = memory.remember(candidate)
        assert result.memory is None
        assert "WRITE_GATE_REJECT_LOW_SIGNAL" in result.decision.reason_codes
        assert memory.store.count() == 0
    finally:
        memory.close(); db.close()


def test_low_confidence_inference_is_not_solidified_as_semantic_fact(tmp_path: Path) -> None:
    _, db, memory = build_memory(tmp_path)
    try:
        result = memory.remember(
            important_candidate(
                kind=MemoryKind.SEMANTIC,
                content="BK provavelmente prefere sempre avatares roxos.",
                provenance=MemoryProvenance(
                    kind=ProvenanceKind.INFERENCE,
                    source="test",
                    actor_id="ENTITY_AMORA",
                    confidence=0.62,
                ),
            )
        )
        assert result.memory is not None
        assert result.memory.kind == MemoryKind.EPISODIC
        assert "LOW_CONFIDENCE_SEMANTIC_DOWNGRADED_TO_EPISODIC" in result.decision.reason_codes
    finally:
        memory.close(); db.close()


def test_auth_never_enters_cognitive_memory(tmp_path: Path) -> None:
    _, db, memory = build_memory(tmp_path)
    try:
        result = memory.remember(important_candidate(privacy_scope=PrivacyScope.AUTH))
        assert result.memory is None
        assert result.decision.reason_codes == ["AUTH_NEVER_COGNITIVE_MEMORY"]
        assert memory.store.count() == 0
    finally:
        memory.close(); db.close()


def test_memory_survives_restart_and_hybrid_recall(tmp_path: Path) -> None:
    clock, db, memory = build_memory(tmp_path)
    result = memory.remember(important_candidate())
    assert result.memory is not None
    memory_id = result.memory.id
    memory.close(); db.close()

    reopened = AmoraDatabase(tmp_path / "amora.db")
    reopened.migrate()
    registry = reopened.load_registry()
    assert registry is not None
    memory2 = MemoryService(MemoryStore(reopened.path, clock, registry), clock)
    try:
        restored = memory2.get(memory_id)
        assert restored is not None
        assert restored.provenance.actor_id == "PERSON_BK"
        hits = memory2.recall(MemoryQuery(text="desenho avatar", entity_ids={"PERSON_BK"}))
        assert hits
        assert hits[0].memory.id == memory_id
        assert "TEXT_MATCH" in hits[0].reason_codes
        assert "ENTITY_MATCH" in hits[0].reason_codes
    finally:
        memory2.close(); reopened.close()


def test_relationship_expansion_contributes_to_recall(tmp_path: Path) -> None:
    _, db, memory = build_memory(tmp_path)
    try:
        result = memory.remember(
            important_candidate(
                kind=MemoryKind.RELATIONAL,
                content="Amora recebeu atenção de BK durante a sessão.",
                entity_refs={"ENTITY_AMORA": 1.0},
                relationship_relevance=1.0,
            )
        )
        assert result.memory is not None
        hits = memory.recall(MemoryQuery(entity_ids={"PERSON_BK"}))
        assert hits
        assert hits[0].memory.id == result.memory.id
        assert "RELATION_MATCH" in hits[0].reason_codes
    finally:
        memory.close(); db.close()


def test_forgetting_reduces_retrievability_without_deleting(tmp_path: Path) -> None:
    _, db, memory = build_memory(tmp_path)
    try:
        result = memory.remember(important_candidate())
        assert result.memory is not None
        memory_id = result.memory.id
        value = memory.forget_softly(memory_id, factor=0.5)
        assert value == 0.5
        still_there = memory.get(memory_id)
        assert still_there is not None
        assert still_there.retrievability == 0.5
    finally:
        memory.close(); db.close()


def test_private_memory_defaults_to_no_remote_processing(tmp_path: Path) -> None:
    _, db, memory = build_memory(tmp_path)
    try:
        result = memory.remember(
            important_candidate(
                privacy_scope=PrivacyScope.PRIVATE,
                remote_processing_allowed=True,
            )
        )
        assert result.memory is not None
        assert result.memory.remote_processing_allowed is False
    finally:
        memory.close(); db.close()


def test_backup_restore_includes_memory(tmp_path: Path) -> None:
    clock, db, memory = build_memory(tmp_path)
    result = memory.remember(important_candidate())
    assert result.memory is not None
    backup = db.backup_to(tmp_path / "backup.db")
    memory.close(); db.close()

    restored_path = AmoraDatabase.restore_backup(backup, tmp_path / "restored.db")
    restored = AmoraDatabase(restored_path)
    restored.migrate()
    registry = restored.load_registry()
    assert registry is not None
    restored_memory = MemoryService(MemoryStore(restored.path, clock, registry), clock)
    try:
        assert restored_memory.store.count() == 1
        hits = restored_memory.recall(MemoryQuery(text="avatar chibi"))
        assert hits and hits[0].memory.content.startswith("BK mostrou")
    finally:
        restored_memory.close(); restored.close()
