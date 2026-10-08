from pathlib import Path
from fastapi.testclient import TestClient
from amora.api import create_api
from amora.app import AmoraCore


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); return AmoraCore.bootstrap()


def test_health_is_public_but_state_requires_auth(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client = TestClient(create_api(core)); assert client.get("/health").status_code == 200; assert client.get("/v1/state").status_code == 401
        token = core.secrets.get("LOCAL_API_TOKEN"); response = client.get("/v1/state", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200; assert response.json()["primary_person"] == "PERSON_BK"
    finally: core.close()


def test_authenticated_event_is_persisted(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client = TestClient(create_api(core)); token = core.secrets.get("LOCAL_API_TOKEN")
        event = {"event_type": "test.api", "source": "pytest", "dedupe_key": "api-dedupe", "payload": {"ok": True}}; headers = {"Authorization": f"Bearer {token}"}
        first = client.post("/v1/events", headers=headers, json=event); second = client.post("/v1/events", headers=headers, json=event)
        assert first.status_code == 200; assert first.json()["deduplicated"] is False; assert second.json()["deduplicated"] is True; assert core.database.event_count() == 1
    finally: core.close()


def test_authenticated_session_uses_device_identity(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client = TestClient(create_api(core)); token = core.secrets.get("LOCAL_API_TOKEN")
        response = client.post("/v1/sessions", headers={"Authorization": f"Bearer {token}"}, json={"device_id": "DEVICE_A15", "privacy_context": "PRIVATE"})
        assert response.status_code == 200; body = response.json(); assert body["device_id"] == "DEVICE_A15"; assert body["authenticated_person_id"] == "PERSON_BK"; assert body["id"] != "PERSON_BK"
    finally: core.close()
