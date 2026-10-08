from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap()


def _client(core: AmoraCore):
    token = core.secrets.get("LOCAL_API_TOKEN")
    return TestClient(create_api(core)), {"Authorization": f"Bearer {token}"}


def test_autonomy_routes_require_authentication(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, _ = _client(core)
        assert client.get("/v1/autonomy/state").status_code == 401
        assert client.get("/v1/autonomy/decisions").status_code == 401
        assert client.post("/v1/autonomy/cycle", json={}).status_code == 401
        assert client.post("/v1/autonomy/drives/social/adjust", json={"delta": 0.3, "reason": "test"}).status_code == 401
    finally:
        core.close()


def test_drive_adjustment_and_cycle_are_idempotent_over_api(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        adjustment = {
            "delta": 0.55,
            "reason": "long silence",
            "dedupe_key": "api-drive-adjustment",
        }
        first_adjust = client.post("/v1/autonomy/drives/social/adjust", headers=headers, json=adjustment)
        assert first_adjust.status_code == 200
        assert first_adjust.json()["deduplicated"] is False
        first_need = first_adjust.json()["drive"]["need"]

        replay_adjust = client.post("/v1/autonomy/drives/social/adjust", headers=headers, json=adjustment)
        assert replay_adjust.status_code == 200
        assert replay_adjust.json()["deduplicated"] is True
        replay_need = replay_adjust.json()["drive"]["need"]
        assert replay_need == pytest.approx(first_need, abs=1e-5)
        assert replay_need < min(1.0, first_need + 1e-5)

        payload = {
            "context": {"is_idle": True, "available_entities": ["PERSON_BK"]},
            "dedupe_key": "api-cycle",
        }
        first = client.post("/v1/autonomy/cycle", headers=headers, json=payload)
        assert first.status_code == 200
        assert first.json()["decision"]["outcome"] == "selected"
        intention_id = first.json()["intention"]["id"]

        replay = client.post("/v1/autonomy/cycle", headers=headers, json=payload)
        assert replay.status_code == 200
        assert replay.json()["deduplicated"] is True
        assert replay.json()["intention"]["id"] == intention_id

        state = client.get("/v1/autonomy/state", headers=headers)
        assert state.status_code == 200
        assert state.json()["current_intention"]["id"] == intention_id
        assert state.json()["decision_count"] == 1
        assert len(state.json()["drives"]) == 7

        decisions = client.get("/v1/autonomy/decisions", headers=headers)
        assert decisions.status_code == 200
        assert len(decisions.json()) == 1
    finally:
        core.close()
