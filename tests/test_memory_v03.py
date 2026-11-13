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


def test_projection_filters_before_top_k(tmp_path):
    _, db, memory = build_memory(tmp_path)
    try:
        for i in range(12):
            assert memory.remember(important_candidate(
                content=f"projeto orquidea confidencial {i}",
                owner_entity_id="ENTITY_CHATGPT", audience={"ENTITY_CHATGPT"},
                importance=1.0,
            )).memory is not None
        allowed = memory.remember(important_candidate(
            content="projeto orquidea autorizado",
            owner_entity_id="PERSON_BK", importance=0.7,
        )).memory
        assert allowed is not None
        hits = memory.recall(MemoryQuery(text="projeto orquidea", limit=1,
            projection_recipient_id="PERSON_BK", projection_remote_only=True))
        assert [hit.memory.id for hit in hits] == [allowed.id]
        assert not memory.recall(MemoryQuery(text="projeto orquidea",
            projection_recipient_id="PERSON_BK", projection_public_only=True))
    finally:
        memory.close(); db.close()


def test_derived_social_scope_is_supported(tmp_path):
    _, db, memory = build_memory(tmp_path)
    try:
        parent = memory.remember(important_candidate(
            privacy_scope=PrivacyScope.SOCIAL, owner_entity_id="PERSON_BK",
        )).memory
        assert parent is not None
        child = memory.remember(important_candidate(
            privacy_scope=PrivacyScope.PUBLIC,
            provenance=MemoryProvenance(kind=ProvenanceKind.DERIVED,
                source="test", derived_from=[parent.id]),
        )).memory
        assert child is not None
        assert child.privacy_scope == PrivacyScope.SOCIAL
        assert child.audience == {"PERSON_BK"}
    finally:
        memory.close(); db.close()


def test_explicit_entity_recall_fallback_is_scoped_and_not_a_text_match(tmp_path):
    _, db, memory = build_memory(tmp_path)
    try:
        own = memory.remember(important_candidate(
            content="Projeto orquidea tem cinco mapas.",
            owner_entity_id="PERSON_BK", entity_refs={"PERSON_BK": 1.0},
        )).memory
        foreign = memory.remember(important_candidate(
            content="Segredo de outra pessoa.",
            owner_entity_id="ENTITY_CHATGPT", entity_refs={"ENTITY_CHATGPT": 1.0},
        )).memory
        assert own and foreign
        query = MemoryQuery(text="lembra daquele?", entity_ids={"PERSON_BK"},
            projection_recipient_id="PERSON_BK", projection_remote_only=True)
        assert memory.recall(query) == []
        hits = memory.recall(query.model_copy(update={"allow_entity_fallback": True}))
        assert [hit.memory.id for hit in hits] == [own.id]
        assert "ENTITY_RECALL_NO_TEXT_MATCH" in hits[0].reason_codes
        assert "TEXT_MATCH" not in hits[0].reason_codes
        assert not memory.recall(query.model_copy(update={
            "allow_entity_fallback": True, "projection_public_only": True,
        }))
    finally:
        memory.close(); db.close()


def test_lexical_recall_reaches_old_records_beyond_importance_cap(tmp_path):
    _, db, memory = build_memory(tmp_path)
    try:
        old = memory.remember(important_candidate(
            content="O projeto orquidea possui cinco mapas.",
            summary="Projeto orquidea",
            tags={"orquidea"}, importance=0.4, owner_entity_id="PERSON_BK",
        )).memory
        assert old is not None
        for i in range(305):
            assert memory.remember(important_candidate(
                content=f"Desenho recente numero {i}", tags={"desenho"},
                importance=1.0, owner_entity_id="PERSON_BK",
            )).memory is not None
        # Also exclude a more important matching record owned by someone else.
        other = memory.remember(important_candidate(
            content="O projeto orquidea é de outra pessoa.",
            importance=1.0, owner_entity_id="ENTITY_CHATGPT",
        )).memory
        assert other is not None
        query = MemoryQuery(text="orquidea", limit=8,
            entity_ids={"PERSON_BK"}, projection_recipient_id="PERSON_BK",
            projection_remote_only=True)
        for use_fts in (True, False):
            memory.store.fts_enabled = use_fts
            hits = memory.recall(query)
            assert any(hit.memory.id == old.id and "TEXT_MATCH" in hit.reason_codes
                       for hit in hits)
            assert all(hit.memory.id != other.id for hit in hits)
    finally:
        memory.close(); db.close()
