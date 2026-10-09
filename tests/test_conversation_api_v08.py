import asyncio
from pathlib import Path

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.conversation import UtteranceKind
from amora.events import PrivacyScope


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap()


def _client(core: AmoraCore):
    token = core.secrets.get("LOCAL_API_TOKEN")
    return TestClient(create_api(core)), {"Authorization": f"Bearer {token}"}


def test_conversation_routes_require_authentication(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, _ = _client(core)
        assert client.get("/v1/conversation/state").status_code == 401
        assert client.get("/v1/conversation/next").status_code == 401
        assert client.get("/v1/conversation/turns").status_code == 401
        assert client.get("/v1/conversation/utterances").status_code == 401
        assert client.post("/v1/conversation/turn", json={"signal": {}}).status_code == 401
    finally:
        core.close()


def test_claim_and_ack_speech_over_api(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        item = asyncio.run(core.conversation.enqueue_text(
            "Papis?",
            target_id="PERSON_BK",
            kind=UtteranceKind.SEMANTIC,
            priority=70,
            privacy_scope=PrivacyScope.PERSONAL,
            source_event_id=None,
            dedupe_key="api-v08-speech",
        ))
        assert item is not None
        client, headers = _client(core)

        claimed = client.get("/v1/conversation/next", headers=headers)
        assert claimed.status_code == 200
        payload = claimed.json()["utterance"]
        assert payload["id"] == str(item.id)
        assert payload["status"] == "claimed"
        assert payload["text"] == "Papis?"

        empty = client.get("/v1/conversation/next", headers=headers)
        assert empty.status_code == 200
        assert empty.json()["utterance"] is None

        ack = client.post(
            f"/v1/conversation/utterances/{item.id}/ack",
            headers=headers,
            json={"spoken": True},
        )
        assert ack.status_code == 200
        assert ack.json()["utterance"]["status"] == "spoken"

        state = client.get("/v1/conversation/state", headers=headers).json()
        assert state["speech_pending_count"] == 0
        assert state["speech_spoken_count"] == 1
    finally:
        core.close()


def test_turn_api_backchannel_is_idempotent(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client, headers = _client(core)
        body = {
            "signal": {
                "speaker_id": "PERSON_BK",
                "addressee_id": "ENTITY_AMORA",
                "conversation_floor": "PERSON_BK",
                "vad_active": True,
                "pause_ms": 260,
                "partial_text": "entendeu",
                "active_conversation": True,
                "dedupe_key": "api-backchannel",
            },
            "allow_backchannel": True,
        }
        first = client.post("/v1/conversation/turn", headers=headers, json=body)
        second = client.post("/v1/conversation/turn", headers=headers, json=body)
        assert first.status_code == second.status_code == 200
        assert first.json()["decision"]["state"] == "BACKCHANNEL"
        assert second.json()["decision"]["deduplicated"] is True
        assert first.json()["utterance"]["id"] == second.json()["utterance"]["id"]
        assert core.conversation.store.pending_count() == 1
    finally:
        core.close()
