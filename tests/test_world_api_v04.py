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


def test_world_routes_require_authentication(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, _ = _client(core)
        assert client.get("/v1/world/state").status_code == 401
        assert client.get("/v1/self").status_code == 401
        assert client.get("/v1/relationships/history", params={"source_id": "PERSON_BK"}).status_code == 401
    finally:
        core.close()


def test_report_is_recorded_but_not_exposed_as_canonical_state(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        response = client.post(
            "/v1/world/record",
            headers=headers,
            json={
                "kind": "report",
                "statement": "BK disse que gosta de café",
                "subject_id": "PERSON_BK",
                "predicate": "likes_coffee",
                "object_value": True,
                "source": "pytest:api",
                "source_entity_id": "PERSON_BK",
                "confidence": 1.0,
            },
        )
        assert response.status_code == 200
        assert response.json()["claim"]["canonical"] is False
        state = client.get("/v1/world/state", headers=headers, params={"subject_id": "PERSON_BK"})
        assert state.status_code == 200
        assert state.json() == []
    finally:
        core.close()


def test_belief_revision_visible_in_state_and_history(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        first = {
            "kind": "belief",
            "statement": "BK não gosta de café",
            "subject_id": "PERSON_BK",
            "predicate": "likes_coffee",
            "object_value": False,
            "source": "pytest:api",
            "confidence": 0.98,
        }
        second = dict(first)
        second.update(statement="BK agora gosta de café", object_value=True, confidence=0.97)
        assert client.post("/v1/world/record", headers=headers, json=first).status_code == 200
        revised = client.post("/v1/world/record", headers=headers, json=second)
        assert revised.status_code == 200
        assert "BELIEF_REVISION" in revised.json()["reason_codes"]

        state = client.get("/v1/world/state", headers=headers, params={"subject_id": "PERSON_BK"}).json()
        assert len(state) == 1
        assert state[0]["object_value"] is True

        history = client.get(
            "/v1/world/history",
            headers=headers,
            params={"subject_id": "PERSON_BK", "predicate": "likes_coffee"},
        ).json()
        assert len(history) == 2
        assert any(item["object_value"] is False and item["valid_until"] is not None for item in history)
    finally:
        core.close()


def test_auth_world_claim_is_rejected(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        response = client.post(
            "/v1/world/record",
            headers=headers,
            json={
                "kind": "observation",
                "statement": "segredo",
                "predicate": "auth_secret",
                "object_value": "token",
                "source": "pytest:api",
                "privacy_scope": "AUTH",
            },
        )
        assert response.status_code == 422
        assert core.world_store.claim_count() == 0
    finally:
        core.close()


def test_self_and_relationship_history_are_readable_when_authenticated(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        self_response = client.get("/v1/self", headers=headers)
        assert self_response.status_code == 200
        assert self_response.json()["core_identity"]["entity_id"] == "ENTITY_AMORA"

        relation_response = client.get(
            "/v1/relationships/history",
            headers=headers,
            params={"source_id": "PERSON_BK", "target_id": "ENTITY_AMORA"},
        )
        assert relation_response.status_code == 200
        assert any(item["relation_type"] == "father_of" for item in relation_response.json())
    finally:
        core.close()
