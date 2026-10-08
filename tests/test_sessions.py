from pathlib import Path
from datetime import datetime, timezone
from amora.identity import DeviceIdentity, DeviceTrust, SessionService, build_initial_registry
from amora.storage import AmoraDatabase
from amora.time import SimulatedClock


def test_device_and_session_are_persistent_and_distinct(tmp_path: Path) -> None:
    path = tmp_path / "amora.db"; db = AmoraDatabase(path); db.migrate(); db.save_registry(build_initial_registry())
    clock = SimulatedClock(datetime(2026, 10, 8, tzinfo=timezone.utc)); service = SessionService(db, clock)
    service.register_device(DeviceIdentity(id="DEVICE_TEST", owner_person_id="PERSON_BK", device_type="android", trust_level=DeviceTrust.LOCAL_TRUSTED, credential_ref="LOCAL_API_TOKEN", last_seen=clock.now()))
    session = service.start_local_session("DEVICE_TEST")
    assert session.device_id == "DEVICE_TEST"; assert session.authenticated_person_id == "PERSON_BK"; assert session.id != "PERSON_BK"; db.close()
    reopened = AmoraDatabase(path)
    try:
        reopened.migrate(); loaded = reopened.get_session(session.id); assert loaded is not None
        assert loaded.device_id == "DEVICE_TEST"; assert loaded.authenticated_person_id == "PERSON_BK"
        assert reopened.active_session_count(clock.now()) == 1
        assert SessionService(reopened, clock).close(session.id) is True
        assert reopened.active_session_count(clock.now()) == 0
    finally:
        reopened.close()
