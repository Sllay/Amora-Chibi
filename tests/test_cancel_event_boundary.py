"""Confirmed cancellation survives only an uncertain *subsequent* audit append."""

import logging

import pytest
from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.observability import JsonFormatter
from test_remote_connection_recovery import fault_remote  # noqa: F401
from test_web_safari_v101 import _enqueue_web


@pytest.mark.parametrize("fault", ["INSERT INTO events", "COMMIT"])
def test_confirmed_cancel_uncertain_event_is_200_without_replay(
    fault_remote, tmp_path, monkeypatch, caplog, fault
):
    backend, server = fault_remote
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "core"))
    core = AmoraCore.bootstrap(storage=backend)
    item = _enqueue_web(core, "Texto continua disponível.", "cancel-event-test")
    headers = {"Authorization": "Bearer " + core.secrets.get("LOCAL_API_TOKEN")}
    # Inject after the canonical cancellation COMMIT, immediately at publish.
    original = core.database.append_event

    def append(event):
        if event.event_type == "conversation.utterance_cancelled":
            server.arm(fault)
        return original(event)

    monkeypatch.setattr(core.database, "append_event", append)
    try:
        with TestClient(create_api(core), raise_server_exceptions=False) as client:
            before = server.count_sql("INSERT INTO events")
            caplog.set_level(logging.INFO)
            response = client.post(
                f"/v1/conversation/utterances/{item.id}/cancel",
                json={"reason": "manual-stop", "speech_only": True}, headers=headers,
            )
            assert response.status_code == 200
            result = response.json()["utterance"]
            assert result["status"] == "cancelled"
            assert result["text"] == item.text
            assert result["metadata"]["cancel_event_persistence"] == "uncertain"
            assert server.count_sql("INSERT INTO events") == before + 1
            assert core.conversation.store.get_utterance(item.id).status.value == "cancelled"
            assert core.database._conn._broken
            # The event-store connection still recovers generically, without replay.
            core.database.event_count()
            assert not core.database._conn._broken
            assert server.count_sql("INSERT INTO events") == before + 1
            record = next(r for r in caplog.records if r.msg == "conversation_cancel_event_persistence_uncertain")
            assert record.correlation_id == response.headers["X-Correlation-ID"]
            assert record.operation == "append_event"
            assert record.canonical_cancel_confirmed is True
            assert core.conversation.state()["cancel_event_persistence"]["uncertain_count"] == 1
            assert any(e["module"] == "conversation.cancel_event" for e in core.operations.snapshot()["errors"])
            logs = "\n".join(JsonFormatter().format(r) for r in caplog.records)
            for private in [item.text, "recovery-db-token", "private-body", "Bearer do-not-log", "INSERT INTO events"]:
                assert private not in logs
    finally:
        core.close()


@pytest.mark.parametrize("fault", ["UPDATE speech_utterances", "COMMIT"])
def test_uncertain_canonical_cancel_still_fails_closed(
    fault_remote, tmp_path, monkeypatch, fault
):
    backend, server = fault_remote
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "core"))
    core = AmoraCore.bootstrap(storage=backend)
    item = _enqueue_web(core, "Texto preservado.", "cancel-domain-test")
    headers = {"Authorization": "Bearer " + core.secrets.get("LOCAL_API_TOKEN")}
    try:
        with TestClient(create_api(core), raise_server_exceptions=False) as client:
            server.arm(fault)
            before = server.count_sql("UPDATE speech_utterances")
            response = client.post(
                f"/v1/conversation/utterances/{item.id}/cancel",
                json={"reason": "stop", "speech_only": True}, headers=headers,
            )
            assert response.status_code == 500
            assert server.count_sql("UPDATE speech_utterances") == before + 1
            assert core.conversation._cancel_event_uncertain_count == 0
    finally:
        core.close()
