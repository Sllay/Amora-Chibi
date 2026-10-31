"""Safari requests over real SQL/transactions with Hrana primary error codes."""

import io
import wave

import httpx
import pytest
from fastapi.testclient import TestClient

from amora.app import AmoraCore
from amora.api import create_api
from amora.audio.stt import GroqWhisperSTT
from amora.storage.remote import LibSQLBackend
from amora.voice.remote import RemoteVoiceBackend
from test_remote_storage import HranaSQLite


@pytest.fixture
def safari(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "core"))
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    server = HranaSQLite(tmp_path / "server.db", primary_constraint_codes=True)

    def transport(request):
        import json

        body = json.loads(request.content)
        response = server(request)
        table = getattr(server, "reject_table", None)
        if table:
            payload = response.json()
            matched = [
                index
                for index, op in enumerate(body["requests"])
                if op.get("stmt", {})
                .get("sql", "")
                .lstrip()
                .startswith("INSERT INTO " + table)
            ]
            if matched:
                # Preserve the full Hrana pipeline response shape (including a
                # bootstrap PRAGMA or close result) and replace only the result
                # belonging to the statement under test. This keeps the fixture
                # valid for both explicit transactions and one-shot autocommit.
                for index in matched:
                    payload["results"][index] = {
                        "type": "error",
                        "error": {
                            "code": "SQLITE_CONSTRAINT",
                            "message": "FOREIGN KEY constraint failed: private-body groq-stt-test-only database-test-only",
                        },
                    }
                return httpx.Response(200, json=payload)
        return response

    backend = LibSQLBackend(
        "https://database.example",
        "database-test-only",
        transport=httpx.MockTransport(transport),
    )
    core = AmoraCore.bootstrap(storage=backend)
    monkeypatch.setenv("AMORA_MODE", "cloud")
    monkeypatch.setenv("AMORA_PUBLIC_ORIGINS", "https://amora.example")
    voice = RemoteVoiceBackend(
        "https://voice.example",
        "voice-test-only",
        transport=httpx.MockTransport(lambda request: httpx.Response(503)),
    )
    try:
        with TestClient(
            create_api(core, voice=voice),
            base_url="https://amora.example",
            raise_server_exceptions=False,
        ) as client:
            token = client.post(
                "/v1/pairing/redeem", json={"code": core.pairing.issue().code}
            ).json()["token"]
            yield core, server, backend, client, {"Authorization": "Bearer " + token}
    finally:
        core.close()


def test_browser_text_cognition_queue_and_voice_failure(safari):
    core, server, backend, client, headers = safari
    result = client.post(
        "/v1/web/utterance",
        headers=headers,
        json={"text": "Oi Amora", "remote_processing_allowed": True, "test_mode": True},
    )
    assert result.status_code == 200, result.text
    assert result.json()["reply_queued"] is not None
    assert core.cognition.store.message_count() >= 2
    item = client.get(
        "/v1/conversation/next?device_id=WEB_SAFARI", headers=headers
    ).json()["utterance"]
    assert item["device_id"] == "WEB_SAFARI" and item["text"]
    assert item["metadata"]["cognitive_turn_id"]
    audio = client.get(f"/v1/web/voice/utterances/{item['id']}/audio", headers=headers)
    assert audio.status_code == 503
    assert audio.json()["detail"] == "VOICE_WORKER_UNAVAILABLE"
    assert core.conversation.store.get_utterance(item["id"]).text == item["text"]
    # Failed TTS did not silently ACK or delete the text. Delivery remains explicit.
    assert core.conversation.store.get_utterance(item["id"]).status.value == "claimed"
    cancelled = client.post(
        f"/v1/conversation/utterances/{item['id']}/cancel",
        headers=headers,
        json={"reason": "test_voice_failure"},
    )
    assert cancelled.status_code == 200
    assert core.conversation.store.get_utterance(item["id"]).text == item["text"]
    assert client.get("/live").json() == {"status": "alive"}
    assert client.get("/ready").status_code == 200
    assert not server.streams


def test_valid_wav_groq_transcript_conversation_queue_ack_and_restart(
    safari, tmp_path, monkeypatch
):
    core, server, backend, client, headers = safari
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(16000)
        output.writeframes(b"\0\0" * 1600)
    wav = buffer.getvalue()
    core.secrets.set("GROQ_API_KEY", "groq-stt-test-only")
    calls = []

    def groq(request):
        assert wav in request.content
        assert request.headers["authorization"] == "Bearer groq-stt-test-only"
        calls.append(request.url.path)
        return httpx.Response(
            200,
            json={
                "text": "Oi Amora",
                "language": "pt",
                "segments": [
                    {"start": 0, "end": 0.1, "no_speech_prob": 0, "avg_logprob": -0.1}
                ],
            },
            headers={"x-request-id": "test-groq-stt"},
        )

    core.stt = GroqWhisperSTT(core.secrets, transport=httpx.MockTransport(groq))
    response = client.post(
        "/v1/audio/transcribe?speaker_id=PERSON_BK&speech_device_id=WEB_SAFARI&remote_processing_allowed=true&test_mode=true",
        headers={**headers, "Content-Type": "audio/wav"},
        content=wav,
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert calls == ["/openai/v1/audio/transcriptions"]
    assert body["accepted"] and body["transcript"]["text"] == "Oi Amora"
    assert body["turn"] and body["reply_queued"]
    item = client.get(
        "/v1/conversation/next?device_id=WEB_SAFARI", headers=headers
    ).json()["utterance"]
    assert item and item["text"]
    assert (
        client.post(
            f"/v1/conversation/utterances/{item['id']}/started",
            headers=headers,
            json={},
        ).status_code
        == 200
    )
    assert (
        client.post(
            f"/v1/conversation/utterances/{item['id']}/ack",
            headers=headers,
            json={"spoken": True},
        ).status_code
        == 200
    )
    assert core.conversation.store.get_utterance(item["id"]).status.value == "spoken"
    assert not list((core.paths.cache / "incoming").glob("*.wav"))
    core.close()
    monkeypatch.setenv("AMORA_MODE", "local")
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "replacement"))
    again = AmoraCore.bootstrap(storage=backend)
    try:
        assert again.database.schema_version() == 11
        assert again.conversation.store.get_utterance(item["id"]).text == item["text"]
        assert (
            again.conversation.store.get_utterance(item["id"]).status.value == "spoken"
        )
        assert any(
            m.raw_text == "Oi Amora" for m in again.cognition.store.list_messages()
        )
    finally:
        again.close()


@pytest.mark.parametrize(
    "route,table,stage",
    [
        ("/v1/web/utterance", "cognitive_journal", "conversation"),
        ("/v1/web/utterance", "events", "language_social"),
        ("/v1/audio/transcribe", "cognitive_journal", "conversation"),
    ],
)
def test_storage_failure_log_has_route_stage_code_without_private_values(
    safari, caplog, route, table, stage
):
    import json
    import logging
    from amora.observability import JsonFormatter
    from amora.audio import Transcript

    core, server, backend, client, headers = safari
    server.reject_table = table
    if route.endswith("transcribe"):

        class AcceptedStt:
            async def transcribe(self, segment):
                return Transcript(
                    text="Oi Amora", provider="test", model="test", language="pt"
                )

        core.stt = AcceptedStt()
        route += "?speaker_id=PERSON_BK&speech_device_id=WEB_SAFARI&test_mode=true"
    caplog.clear()
    with caplog.at_level(logging.ERROR, logger="amora.api"):
        if "/audio/" in route:
            response = client.post(
                route,
                headers={**headers, "Content-Type": "audio/wav"},
                content=b"RIFF-test",
            )
        else:
            response = client.post(
                route,
                headers=headers,
                json={
                    "text": "Oi Amora",
                    "remote_processing_allowed": True,
                    "test_mode": True,
                },
            )
    assert response.status_code == 500
    records = [
        r for r in caplog.records if r.name == "amora.api" and r.msg == "request_failed"
    ]
    assert len(records) == 1
    data = json.loads(JsonFormatter().format(records[0]))
    assert data["method"] == "POST" and data["path"] == route.split("?")[0]
    assert data["stage"] == stage
    assert data["storage_error_code"] == "SQLITE_CONSTRAINT"
    assert data["exception_type"] == "DatabaseError"
    assert data["correlation_id"] and data["latency_ms"] >= 0
    if table == "cognitive_journal":
        assert data["storage_module"] == "amora.cognition.store"
        assert data["operation"] == "put_journal"
    rendered = "\n".join(JsonFormatter().format(record) for record in caplog.records)
    for private in [
        "private-body",
        "groq-stt-test-only",
        "database-test-only",
        headers["Authorization"],
        "Oi Amora",
    ]:
        assert private not in rendered
    assert not server.streams


def test_unknown_database_error_details_never_become_structured_log_fields():
    import json
    import sqlite3
    from amora.observability import request_failure_fields

    error = sqlite3.DatabaseError(
        "Bearer private-provider-key; private conversation body"
    )
    error.sqlite_errorname = "PRIVATE_PROVIDER_KEY"
    fields = request_failure_fields(error)
    assert fields["storage_error_code"] == "SQL_ERROR"
    assert "private" not in json.dumps(fields).lower()
