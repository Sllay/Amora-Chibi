"""MM.00D.1: immutable sensory delivery on the existing SQLite/Hrana store."""
from datetime import timedelta, timezone
import sqlite3
from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.events import AmoraEvent
from amora.events.multimodal import build_observation_event, parse_observation_event
from amora.storage import AmoraDatabase
from remote_storage_contracts_v13_source import remote
from test_multimodal_contract_mm00 import NOW, context, observation


@pytest.mark.parametrize("mode", ["sqlite", "hrana"])
def test_durable_observation_replay_and_conflicts(remote, tmp_path, mode):
    backend, server = remote
    backend = backend if mode == "hrana" else None
    path = tmp_path / "observations.db"
    first = build_observation_event(context(), observation())
    db = AmoraDatabase(path, backend=backend)
    try:
        db.migrate()
        assert db.append_event(first)
        assert db.get_observation_event(first.id) == first
    finally:
        db.close()
    db = AmoraDatabase(path, backend=backend)
    try:
        other_zone = timezone(timedelta(hours=-3))
        replay = build_observation_event(context(), observation(
            capture_started_at=NOW.astimezone(other_zone),
            capture_ended_at=(NOW + timedelta(seconds=1)).astimezone(other_zone),
            received_at=NOW + timedelta(seconds=20),
        ))
        assert replay.id == first.id
        assert not db.append_event(replay)
        assert db.get_observation_event(first.id) == first
        conflicts = (
            build_observation_event(context(), observation(text="different evidence")),
            build_observation_event(context(actor_id="PERSON_LUCAS"), observation()),
            build_observation_event(context(audience=("PERSON_LUCAS",)), observation()),
            build_observation_event(context(remote_processing_allowed=True), observation()),
            build_observation_event(context(), observation(confidence=.7)),
            build_observation_event(context(), observation(model="another-model")),
            build_observation_event(context(), observation(capture_ended_at=NOW + timedelta(seconds=3))),
        )
        for conflict in conflicts:
            assert conflict.id == first.id
            with pytest.raises(sqlite3.IntegrityError):
                db.append_event(conflict)
            assert db.get_observation_event(first.id) == first
        assert db.event_count() == 1
        assert db.get_observation_event(UUID(int=99)) is None
        generic = AmoraEvent(event_type="test.other", source="test", dedupe_key="other")
        assert db.append_event(generic)
        assert db.get_observation_event(generic.id) is None
        assert db.event_count() == 2
    finally:
        db.close()
    if mode == "hrana":
        assert not server.streams


def test_forged_envelope_and_corrupt_row_fail_closed(tmp_path):
    db = AmoraDatabase(tmp_path / "observations.db")
    try:
        db.migrate()
        valid = build_observation_event(context(), observation())
        for forged in (
            valid.model_copy(update={"id": UUID(int=88)}),
            valid.model_copy(update={"actor": "PERSON_LUCAS", "sequence": 90}),
            valid.model_copy(update={"payload": {**valid.payload, "raw_frame": "secret"}}),
            valid.model_copy(update={"payload": {**valid.payload, "version": True}}),
        ):
            with pytest.raises(ValueError, match="^PERCEPTION_EVENT_INVALID$"):
                db.append_event(forged)
        assert db.event_count() == 0
        assert db.append_event(valid)
        ctx, obs = parse_observation_event(valid)
        assert ctx == context()
        assert obs == observation()
        with db._conn:
            db._conn.execute("UPDATE events SET payload_json=? WHERE id=?", ("{}", str(valid.id)))
        with pytest.raises(ValueError, match="^PERCEPTION_EVENT_INVALID$"):
            db.get_observation_event(valid.id)
    finally:
        db.close()


def test_generic_http_event_route_rejects_reserved_sensory_event(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "amora"))
    monkeypatch.setenv("AMORA_MODE", "local")
    core = AmoraCore.bootstrap()
    try:
        token = core.secrets.get("LOCAL_API_TOKEN")
        headers = {"Authorization": f"Bearer {token}"}
        client = TestClient(create_api(core))
        before = core.database.event_count()
        event = build_observation_event(context(), observation())
        response = client.post("/v1/events", headers=headers, json=event.model_dump(mode="json"))
        assert response.status_code == 403
        assert response.json()["detail"] == "PERCEPTION_EVENT_INTERNAL_ONLY"
        assert core.database.event_count() == before
    finally:
        core.close()
