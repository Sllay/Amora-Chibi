import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.autonomy import DecisionContext, DriveType


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap()


def _client(core: AmoraCore):
    token = core.secrets.get("LOCAL_API_TOKEN")
    return TestClient(create_api(core)), {"Authorization": f"Bearer {token}"}


def _seed_intention(core: AmoraCore) -> None:
    core.autonomy.adjust_drive(DriveType.SOCIAL, 0.55, reason="api test", dedupe_key="api-v07-drive")
    result = asyncio.run(
        core.autonomy.cycle(
            DecisionContext(is_idle=True, available_entities={"PERSON_BK"}),
            dedupe_key="api-v07-cycle",
        )
    )
    assert result.intention is not None


def test_action_routes_require_authentication(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, _ = _client(core)
        assert client.get("/v1/actions/state").status_code == 401
        assert client.get("/v1/actions/plans").status_code == 401
        assert client.get("/v1/actions/executions").status_code == 401
        assert client.post("/v1/actions/plan", json={}).status_code == 401
        assert client.post("/v1/actions/execute", json={}).status_code == 401
    finally:
        core.close()


def test_plan_execute_and_replay_over_api(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        _seed_intention(core)
        client, headers = _client(core)

        plan = client.post("/v1/actions/plan", headers=headers, json={"dedupe_key": "api-v07-plan"})
        assert plan.status_code == 200
        plan_id = plan.json()["id"]
        assert plan.json()["skill_name"] == "core.semantic_intention"

        execute = client.post(
            "/v1/actions/execute",
            headers=headers,
            json={"plan_id": plan_id, "dedupe_key": "api-v07-execute"},
        )
        assert execute.status_code == 200
        assert execute.json()["plan"]["status"] == "completed"
        assert execute.json()["executions"][0]["verified"] is True

        replay = client.post(
            "/v1/actions/execute",
            headers=headers,
            json={"plan_id": plan_id, "dedupe_key": "api-v07-execute"},
        )
        assert replay.status_code == 200
        assert replay.json()["deduplicated"] is True

        state = client.get("/v1/actions/state", headers=headers)
        assert state.status_code == 200
        assert state.json()["plugin_count"] == 1
        assert state.json()["skill_count"] == 2
        assert state.json()["action_run_count"] == 1
        assert state.json()["tool_execution_count"] == 1
    finally:
        core.close()
