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


def _anger_payload() -> dict[str, object]:
    return {
        "description": "BK ignorou Amora de propósito",
        "outcome_valence": -0.9,
        "target_id": "PERSON_BK",
        "agent_id": "PERSON_BK",
        "goal_relevance": 0.9,
        "novelty": 0.4,
        "expectedness": 0.5,
        "agency": 0.95,
        "control": 0.6,
        "intentionality": 0.95,
        "norm_violation": 0.85,
        "relationship_relevance": 0.95,
    }


def test_emotion_routes_require_authentication(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, _ = _client(core)
        assert client.get("/v1/emotion/state").status_code == 401
        assert client.get("/v1/emotion/affect/PERSON_BK").status_code == 401
        assert client.get("/v1/emotion/events").status_code == 401
        assert client.get("/v1/emotion/bids").status_code == 401
        assert client.post("/v1/emotion/appraise", json=_anger_payload()).status_code == 401
    finally:
        core.close()


def test_appraisal_route_updates_directed_affect_mood_and_bid(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        response = client.post("/v1/emotion/appraise", headers=headers, json=_anger_payload())
        assert response.status_code == 200
        body = response.json()
        assert body["event"]["emotion"] == "anger"
        assert body["event"]["target_id"] == "PERSON_BK"
        assert body["target_affect"]["anger"] > 0.5
        assert body["mood"]["valence"] < 0
        assert body["social_bids"][0]["bid_type"] == "set_boundary"

        bk = client.get("/v1/emotion/affect/PERSON_BK", headers=headers)
        chatgpt = client.get("/v1/emotion/affect/ENTITY_CHATGPT", headers=headers)
        assert bk.status_code == 200 and bk.json()["anger"] > 0.5
        assert chatgpt.status_code == 200 and chatgpt.json()["anger"] == 0.0

        state = client.get("/v1/emotion/state", headers=headers)
        assert state.status_code == 200
        assert state.json()["pending_bids"]
    finally:
        core.close()


def test_auth_appraisal_is_rejected_and_not_persisted(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        payload = _anger_payload() | {"privacy_scope": "AUTH"}
        response = client.post("/v1/emotion/appraise", headers=headers, json=payload)
        assert response.status_code == 422
        assert core.emotion.store.event_count() == 0
    finally:
        core.close()


def test_unknown_target_is_rejected(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        payload = _anger_payload() | {"target_id": "PERSON_UNKNOWN", "agent_id": "PERSON_UNKNOWN"}
        response = client.post("/v1/emotion/appraise", headers=headers, json=payload)
        assert response.status_code == 422
        assert core.emotion.store.event_count() == 0
    finally:
        core.close()


def test_social_bid_can_be_consumed_without_executing_action(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        body = client.post("/v1/emotion/appraise", headers=headers, json=_anger_payload()).json()
        bid_id = body["social_bids"][0]["id"]
        response = client.post(f"/v1/emotion/bids/{bid_id}/consume", headers=headers)
        assert response.status_code == 200
        assert response.json()["consumed"] is True
        assert client.get("/v1/emotion/bids", headers=headers).json() == []
        assert core.emotion.store.event_count() == 1
    finally:
        core.close()
