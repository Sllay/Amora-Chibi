from pathlib import Path
from amora.events import AmoraEvent
from amora.storage import AmoraDatabase


def test_database_migrates_and_deduplicates_event(tmp_path: Path) -> None:
    db = AmoraDatabase(tmp_path / "amora.db")
    try:
        assert db.migrate() == 1
        assert db.migrate() == 0
        event = AmoraEvent(event_type="test", source="pytest", dedupe_key="abc")
        assert db.append_event(event) is True
        duplicate = AmoraEvent(event_type="test", source="pytest", dedupe_key="abc")
        assert db.append_event(duplicate) is False
        assert db.integrity_check() == "ok"
    finally:
        db.close()
