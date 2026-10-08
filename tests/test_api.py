from pathlib import Path

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap()


def _client(core: AmoraCore):
    token = core.secrets.get("LOCAL_API_TOKEN")
    return TestClient(create_api(core)), {"Authorization": f"Bearer {token}"}


def test_health_is_public_but_state_requires_auth(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        assert client.get("/health").status_code == 200
        assert client.get("/v1/state").status_code == 401
        response = client.get("/v1/state", headers=headers)
        assert response.status_code == 200
        assert response.json()["primary_person"] == "PERSON_BK"
    finally:
        core.close()


def test_authenticated_event_is_persisted(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        event = {
            "event_type": "test.api",
            "source": "pytest",
            "dedupe_key": "api-dedupe",
            "payload": {"ok": True},
        }
        first = client.post("/v1/events", headers=headers, json=event)
        second = client.post("/v1/events", headers=headers, json=event)
        assert first.status_code == 200
        assert first.json()["deduplicated"] is False
        assert second.json()["deduplicated"] is True
        assert core.database.event_count() == 1
    finally:
        core.close()


def test_authenticated_session_uses_device_identity(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        response = client.post(
            "/v1/sessions",
            headers=headers,
            json={"device_id": "DEVICE_A15", "privacy_context": "PRIVATE"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["device_id"] == "DEVICE_A15"
        assert body["authenticated_person_id"] == "PERSON_BK"
        assert body["id"] != "PERSON_BK"
    finally:
        core.close()


def test_language_addressee_and_reference_resolution_are_authenticated(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        assert client.post(
            "/v1/language/addressee",
            json={"text": "Amora, olha isso", "speaker_id": "PERSON_BK"},
        ).status_code == 401

        addressee = client.post(
            "/v1/language/addressee",
            headers=headers,
            json={"text": "Amora, olha isso", "speaker_id": "PERSON_BK"},
        )
        assert addressee.status_code == 200
        assert addressee.json()["entity_id"] == "ENTITY_AMORA"

        resolution = client.post(
            "/v1/language/resolve",
            headers=headers,
            json={
                "expression": "sua mãe",
                "speaker_id": "PERSON_BK",
                "addressee_id": "ENTITY_AMORA",
            },
        )
        assert resolution.status_code == 200
        assert resolution.json()["entity_ids"] == ["ENTITY_CHATGPT"]
    finally:
        core.close()


def test_language_observe_updates_scene_and_persists_raw_observation(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        before = core.database.event_count()
        response = client.post(
            "/v1/language/observe",
            headers=headers,
            json={
                "text": "Amora, vem cá",
                "speaker_id": "PERSON_BK",
                "session_id": "SESSION_TEST",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["text"] == "Amora, vem cá"
        assert body["addressee"]["entity_id"] == "ENTITY_AMORA"
        assert core.database.event_count() == before + 1
        assert core.social.scene.active_addressee == "ENTITY_AMORA"
    finally:
        core.close()


def test_language_auth_observation_is_redacted_and_not_persisted(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        before = core.database.event_count()
        response = client.post(
            "/v1/language/observe",
            headers=headers,
            json={
                "text": "Amora, meu token é segredo",
                "speaker_id": "PERSON_BK",
                "privacy_scope": "AUTH",
            },
        )
        assert response.status_code == 200
        assert response.json()["text"] is None
        assert core.database.event_count() == before
    finally:
        core.close()
