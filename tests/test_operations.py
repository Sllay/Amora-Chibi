import json
from datetime import timedelta
from uuid import uuid4
from fastapi.testclient import TestClient
from amora.api import create_api
from amora.operations import OperationMonitor
from amora.time import SimulatedClock
from test_web_safari_v101 import _client
from test_actions_api_v07 import _seed_intention


def test_errors_group_by_code_not_turn_and_are_bounded():
    clock = SimulatedClock()
    monitor = OperationMonitor(clock, error_capacity=2)
    first = monitor.error("voice", "UNAVAILABLE", correlation_id=uuid4())
    clock.advance(timedelta(seconds=2))
    assert monitor.error("voice", "UNAVAILABLE", correlation_id=uuid4()) == first
    group = monitor.snapshot()["errors"][0]
    assert group["occurrence_count"] == 2
    assert group["last_seen"] != group["first_seen"]
    monitor.error("storage", "TIMEOUT")
    monitor.error("api", "FAILED")
    assert len(monitor.snapshot()["errors"]) == 2


def test_operational_projection_is_authenticated_and_excludes_context(
    tmp_path, monkeypatch
):
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        assert client.get("/v1/operations/state").status_code == 401
        response = client.post(
            "/v1/web/utterance", json={"text": "Amora, oi."}, headers=headers
        )
        assert response.status_code == 200
        report = client.get("/v1/operations/state", headers=headers)
        assert report.status_code == 200
        body = report.json()
        assert body["schema"] == "amora.operations.v1"
        assert body["health"]["status"] == "ready"
        assert body["modules"]["voice"] == "DEGRADED"
        assert "Amora, oi." not in report.text
        assert core.secrets.get("LOCAL_API_TOKEN") not in report.text
        assert "last_realization" not in report.text
        assert "X-Correlation-ID" in report.headers
    finally:
        core.close()


def test_action_lifecycle_is_observed_without_arguments_or_output(
    tmp_path, monkeypatch
):
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        _seed_intention(core)
        plan = client.post(
            "/v1/actions/plan", json={"dedupe_key": "ops-plan"}, headers=headers
        ).json()
        run = client.post(
            "/v1/actions/execute",
            json={"plan_id": plan["id"], "dedupe_key": "ops-execute"},
            headers=headers,
        )
        assert run.status_code == 200
        body = client.get("/v1/operations/state", headers=headers).json()
        states = [item["state"] for item in body["events"] if item["kind"] == "action"]
        assert states == ["requested", "running", "succeeded"]
        assert body["events"][-1]["latency_ms"] >= 0
        assert all(
            "output" not in event and "arguments" not in event
            for event in body["events"]
        )
    finally:
        core.close()


def test_repeated_voice_failure_is_aggregated(tmp_path, monkeypatch):
    from test_web_safari_v101 import _enqueue_web
    from amora.voice.local_amora import LocalVoiceUnavailable

    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        item = _enqueue_web(core, "Oi.", "ops-error")

        def fail(_):
            raise LocalVoiceUnavailable("private-detail")

        monkeypatch.setattr(client.app.state.web_local_voice, "render", fail)
        for _ in range(2):
            assert (
                client.get(
                    f"/v1/web/voice/utterances/{item.id}/audio", headers=headers
                ).status_code
                == 503
            )
        report = client.get("/v1/operations/state", headers=headers).json()
        errors = [e for e in report["errors"] if e["module"] == "voice"]
        assert len(errors) == 1 and errors[0]["occurrence_count"] == 2
        assert "private-detail" not in json.dumps(report)
    finally:
        core.close()


def test_job_snapshot_uses_injected_clock():
    from amora.time import Scheduler

    clock = SimulatedClock()
    scheduler = Scheduler(clock)
    scheduler.schedule_once("probe", 5, lambda: None)
    assert scheduler.snapshot()[0]["due_in_seconds"] == 5
    clock.advance(timedelta(seconds=2))
    assert scheduler.snapshot()[0]["due_in_seconds"] == 3


def test_websocket_excludes_secrets_and_unsubscribes_on_idle_disconnect(
    tmp_path, monkeypatch
):
    from amora.events import AmoraEvent, PrivacyScope

    core, _, headers = _client(tmp_path, monkeypatch)
    try:
        with TestClient(create_api(core)) as client:
            baseline = len(core.events._handlers.get("*", []))
            with client.websocket_connect(
                "/v1/events/stream", headers=headers
            ) as websocket:
                for scope in [PrivacyScope.SECRET, PrivacyScope.PUBLIC]:
                    event = AmoraEvent(
                        event_type="probe", source="test", privacy_scope=scope
                    )
                    assert (
                        client.post(
                            "/v1/events",
                            json=event.model_dump(mode="json"),
                            headers=headers,
                        ).status_code
                        == 200
                    )
                assert (
                    websocket.receive_json()["privacy_scope"]
                    == PrivacyScope.PUBLIC.value
                )
            assert len(core.events._handlers.get("*", [])) == baseline
            with client.websocket_connect("/v1/events/stream", headers=headers):
                pass  # disconnect without waiting for an event
            assert len(core.events._handlers.get("*", [])) == baseline
    finally:
        core.close()


def test_denied_action_has_terminal_error_group(tmp_path, monkeypatch):
    import asyncio
    from amora.actions.models import ToolCall
    from uuid import uuid4

    core, _, _ = _client(tmp_path, monkeypatch)
    try:
        call = ToolCall(
            plan_id=uuid4(),
            step_id=uuid4(),
            intention_id=uuid4(),
            plugin_id="missing",
            tool_name="missing",
            created_at=core.clock.now(),
        )
        result = asyncio.run(
            core.actions.bus.dispatch(call, core.actions.default_context())
        )
        assert result.status.value == "denied"
        snapshot = core.operations.snapshot()
        assert [item["state"] for item in snapshot["events"]] == ["requested", "denied"]
        assert snapshot["errors"][0]["error_code"] == "TOOL_NOT_REGISTERED"
    finally:
        core.close()
