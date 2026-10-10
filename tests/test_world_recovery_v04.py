from pathlib import Path

from amora.app import AmoraCore
from amora.storage import AmoraDatabase
from amora.time import SystemClock
from amora.world import EpistemicKind, EvidenceKind, SelfLayer, WorldClaimDraft, WorldModelService, WorldStore


def test_promoting_report_to_belief_preserves_claim_evidence(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); core = AmoraCore.bootstrap()
    try:
        report = core.world.record(WorldClaimDraft(kind=EpistemicKind.REPORT, statement="BK disse que prefere desenhar de noite", subject_id="PERSON_BK", predicate="preferred_drawing_period", object_value="night", source="test:report", source_entity_id="PERSON_BK", confidence=1.0)).claim
        assert report.canonical is False
        promoted = core.world.promote_to_belief(str(report.id), confidence=0.96).claim
        assert promoted.canonical is True and promoted.supersedes_claim_id is None
        assert any(item.kind == EvidenceKind.CLAIM and item.ref_id == str(report.id) for item in promoted.evidence)
        assert core.world.current_state(subject_id="PERSON_BK")[0].id == promoted.id
    finally: core.close()


def test_self_model_preference_survives_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); first = AmoraCore.bootstrap()
    try: first.self_model.set(SelfLayer.PREFERENCES, "favorite_visual_style", "chibi", source="test", confidence=0.82)
    finally: first.close()
    second = AmoraCore.bootstrap()
    try:
        entry = second.self_model.get(SelfLayer.PREFERENCES, "favorite_visual_style"); assert entry is not None and entry.value == "chibi" and entry.confidence == 0.82
        assert second.self_model.get(SelfLayer.CORE_IDENTITY, "entity_id").value == "ENTITY_AMORA"
    finally: second.close()


def test_world_model_survives_backup_restore_with_history(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); core = AmoraCore.bootstrap()
    try:
        core.world.record(WorldClaimDraft(kind=EpistemicKind.BELIEF, statement="BK prefere azul", subject_id="PERSON_BK", predicate="favorite_color", object_value="blue", source="test:first", confidence=0.97))
        core.world.record(WorldClaimDraft(kind=EpistemicKind.BELIEF, statement="BK agora prefere roxo", subject_id="PERSON_BK", predicate="favorite_color", object_value="purple", source="test:second", confidence=0.98)); backup = core.create_backup("v04-world.db")
    finally: core.close()
    restored_path = AmoraDatabase.restore_backup(backup, tmp_path / "restored.db"); restored_db = AmoraDatabase(restored_path)
    try:
        assert restored_db.integrity_check() == "ok" and restored_db.schema_version() == 10
    finally: restored_db.close()
    store = WorldStore(restored_path, SystemClock()); world = WorldModelService(store, SystemClock())
    try:
        current = world.current_state(subject_id="PERSON_BK"); assert len(current) == 1 and current[0].object_value == "purple"
        history = world.history(subject_id="PERSON_BK", predicate="favorite_color"); assert len(history) == 2
        old = next(item for item in history if item.object_value == "blue"); new = next(item for item in history if item.object_value == "purple")
        assert old.valid_until == new.valid_from and new.supersedes_claim_id == old.id
    finally: store.close()


def test_relationship_revision_history_survives_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); first = AmoraCore.bootstrap()
    try:
        initial = first.world_store.current_relationship("PERSON_BK", "father_of", "ENTITY_AMORA"); assert initial is not None
        first.relationships.record("PERSON_BK", "father_of", "ENTITY_AMORA", confidence=0.95, source_of_truth="test:revalidated")
    finally: first.close()
    second = AmoraCore.bootstrap()
    try:
        history = [item for item in second.relationships.history("PERSON_BK", target_id="ENTITY_AMORA") if item.relation_type == "father_of"]
        assert len(history) == 2 and history[0].source_of_truth == "test:revalidated" and history[1].valid_until == history[0].valid_from
    finally: second.close()
