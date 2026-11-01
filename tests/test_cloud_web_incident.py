"""Cloud/Safari incident regressions aligned with schema migration 14."""

from cloud_web_incident_v13_source import *  # noqa: F401,F403


def test_valid_wav_groq_transcript_conversation_queue_ack_and_restart(safari, tmp_path, monkeypatch):
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
        return httpx.Response(200, json={"text": "Oi Amora", "language": "pt", "segments": [{"start": 0, "end": 0.1, "no_speech_prob": 0, "avg_logprob": -0.1}]}, headers={"x-request-id": "test-groq-stt"})

    core.stt = GroqWhisperSTT(core.secrets, transport=httpx.MockTransport(groq))
    response = client.post("/v1/audio/transcribe?speaker_id=PERSON_BK&speech_device_id=WEB_SAFARI&remote_processing_allowed=true&test_mode=true", headers={**headers, "Content-Type": "audio/wav"}, content=wav)
    assert response.status_code == 200, response.text
    body = response.json()
    assert calls == ["/openai/v1/audio/transcriptions"]
    assert body["accepted"] and body["transcript"]["text"] == "Oi Amora"
    assert body["turn"] and body["reply_queued"]
    item = client.get("/v1/conversation/next?device_id=WEB_SAFARI", headers=headers).json()["utterance"]
    assert item and item["text"]
    assert client.post(f"/v1/conversation/utterances/{item['id']}/started", headers=headers, json={}).status_code == 200
    assert client.post(f"/v1/conversation/utterances/{item['id']}/ack", headers=headers, json={"spoken": True}).status_code == 200
    assert core.conversation.store.get_utterance(item["id"]).status.value == "spoken"
    assert not list((core.paths.cache / "incoming").glob("*.wav"))
    core.close()
    monkeypatch.setenv("AMORA_MODE", "local")
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "replacement"))
    again = AmoraCore.bootstrap(storage=backend)
    try:
        assert again.database.schema_version() == 14
        assert again.conversation.store.get_utterance(item["id"]).text == item["text"]
        assert again.conversation.store.get_utterance(item["id"]).status.value == "spoken"
        assert any(m.raw_text == "Oi Amora" for m in again.cognition.store.list_messages())
    finally:
        again.close()
