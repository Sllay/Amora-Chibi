from pathlib import Path
from amora.events import AmoraEvent, AsyncEventBus
from amora.identity import build_initial_registry
from amora.storage import AmoraDatabase


def test_database_migrates_and_deduplicates_event(tmp_path: Path) -> None:
    db = AmoraDatabase(tmp_path / "amora.db")
    try:
        assert db.migrate() == 8
        assert db.migrate() == 0
        assert db.schema_version() == 8
        event = AmoraEvent(event_type="test", source="pytest", dedupe_key="abc")
        assert db.append_event(event) is True
        duplicate = AmoraEvent(event_type="test", source="pytest", dedupe_key="abc")
        assert db.append_event(duplicate) is False
        assert db.event_count() == 1
        assert db.integrity_check() == "ok"
    finally:
        db.close()


def test_registry_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "amora.db"
    db = AmoraDatabase(path)
    db.migrate(); db.save_registry(build_initial_registry()); db.close()
    reopened = AmoraDatabase(path)
    try:
        reopened.migrate(); registry = reopened.load_registry(); assert registry is not None
        assert registry.resolve_alias("Slay").id == "PERSON_BK"
        assert registry.resolve_alias("Nick").id == "PERSON_BK"
        assert registry.related("PERSON_BK", "father_of")[0].id == "ENTITY_AMORA"
    finally:
        reopened.close()


async def test_event_deduplication_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "amora.db"; db = AmoraDatabase(path); db.migrate(); bus = AsyncEventBus(event_store=db)
    assert (await bus.publish(AmoraEvent(event_type="x", source="test", dedupe_key="persistent"))).deduplicated is False
    db.close(); reopened = AmoraDatabase(path); reopened.migrate(); bus2 = AsyncEventBus(event_store=reopened)
    try:
        result = await bus2.publish(AmoraEvent(event_type="x", source="test", dedupe_key="persistent"))
        assert result.deduplicated is True; assert reopened.event_count() == 1
    finally:
        reopened.close()


def test_backup_restore_roundtrip(tmp_path: Path) -> None:
    original = AmoraDatabase(tmp_path / "amora.db"); original.migrate(); original.append_event(AmoraEvent(event_type="backup.test", source="pytest")); backup = original.backup_to(tmp_path / "backup.db"); original.close()
    restored_path = AmoraDatabase.restore_backup(backup, tmp_path / "restored.db"); restored = AmoraDatabase(restored_path)
    try:
        assert restored.integrity_check() == "ok"; assert restored.event_count() == 1; assert restored.schema_version() == 8
    finally:
        restored.close()
