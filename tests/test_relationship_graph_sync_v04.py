from pathlib import Path

from amora.app import AmoraCore


def test_relationship_revision_updates_registry_and_survives_restart(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    first = AmoraCore.bootstrap()
    try:
        revised = first.relationships.record(
            "PERSON_BK",
            "father_of",
            "ENTITY_AMORA",
            confidence=0.94,
            source_of_truth="test:graph-sync",
        )
        graph = first.identities.relationship("PERSON_BK", "father_of", "ENTITY_AMORA")
        assert graph is not None
        assert graph.confidence == 0.94
        assert revised.confidence == 0.94
    finally:
        first.close()

    second = AmoraCore.bootstrap()
    try:
        graph = second.identities.relationship("PERSON_BK", "father_of", "ENTITY_AMORA")
        assert graph is not None
        assert graph.confidence == 0.94
        current = second.world_store.current_relationship("PERSON_BK", "father_of", "ENTITY_AMORA")
        assert current is not None
        assert current.confidence == 0.94
        assert current.source_of_truth == "test:graph-sync"
    finally:
        second.close()
