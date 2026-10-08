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


def _candidate(**overrides):
    body = {
        "kind": "episodic",
        "content": "BK mostrou um desenho de roupa nova para Amora.",
        "provenance": {
            "kind": "observation",
            "source": "pytest:api",
            "confidence": 0.98,
            "actor_id": "PERSON_BK",
        },
        "entity_refs": {"PERSON_BK": 1.0, "ENTITY_AMORA": 1.0},
        "tags": ["desenho", "roupa"],
        "importance": 0.82,
        "novelty": 0.9,
        "relationship_relevance": 0.75,
        "future_consequence": 0.55,
        "rarity": 0.5,
        "goal_relevance": 0.7,
    }
    body.update(overrides)
    return body


def test_memory_routes_require_authentication(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, _ = _client(core)
        assert client.post("/v1/memory/remember", json=_candidate()).status_code == 401
        assert client.post("/v1/memory/recall", json={"text": "desenho"}).status_code == 401
    finally:
        core.close()


def test_memory_roundtrip_through_local_api(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        stored = client.post("/v1/memory/remember", headers=headers, json=_candidate())
        assert stored.status_code == 200
        body = stored.json()
        assert body["decision"]["store"] is True
        memory_id = body["memory"]["id"]

        recalled = client.post(
            "/v1/memory/recall",
            headers=headers,
            json={"text": "desenho roupa", "entity_ids": ["PERSON_BK"]},
        )
        assert recalled.status_code == 200
        hits = recalled.json()
        assert hits and hits[0]["memory"]["id"] == memory_id
        assert "TEXT_MATCH" in hits[0]["reason_codes"]

        fetched = client.get(f"/v1/memory/{memory_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["provenance"]["source"] == "pytest:api"
    finally:
        core.close()


def test_auth_candidate_is_refused_by_api_without_persistence(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        before = core.memory.store.count()
        response = client.post(
            "/v1/memory/remember",
            headers=headers,
            json=_candidate(privacy_scope="AUTH"),
        )
        assert response.status_code == 200
        body = response.json()
        assert body["decision"]["store"] is False
        assert body["decision"]["reason_codes"] == ["AUTH_NEVER_COGNITIVE_MEMORY"]
        assert body["memory"] is None
        assert core.memory.store.count() == before
    finally:
        core.close()


def test_private_memory_cannot_opt_into_remote_processing(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        response = client.post(
            "/v1/memory/remember",
            headers=headers,
            json=_candidate(privacy_scope="PRIVATE", remote_processing_allowed=True),
        )
        assert response.status_code == 200
        assert response.json()["memory"]["remote_processing_allowed"] is False
    finally:
        core.close()


def test_soft_forget_via_api_keeps_memory_but_reduces_retrievability(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        stored = client.post("/v1/memory/remember", headers=headers, json=_candidate()).json()
        memory_id = stored["memory"]["id"]
        response = client.post(f"/v1/memory/{memory_id}/forget?factor=0.5", headers=headers)
        assert response.status_code == 200
        assert response.json()["retrievability"] == 0.5
        fetched = client.get(f"/v1/memory/{memory_id}", headers=headers)
        assert fetched.status_code == 200
        assert fetched.json()["retrievability"] == 0.5
    finally:
        core.close()
