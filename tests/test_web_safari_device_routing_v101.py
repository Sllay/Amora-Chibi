from pathlib import Path

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.audio import Transcript


class FakeWebStt:
    async def transcribe(self, segment):
        assert segment.mime_type == "audio/wav"
        return Transcript(
            text="Amora, fala comigo.",
            provider="fake",
            model="fake-stt",
            language=segment.language,
            request_id="req-web-routing",
        )


def _client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap()
    client = TestClient(create_api(core))
    token = core.secrets.get("LOCAL_API_TOKEN")
    assert token
    headers = {"Authorization": f"Bearer {token}"}
    return core, client, headers


def test_web_text_reply_is_claimable_by_safari_not_android(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        response = client.post(
            "/v1/web/utterance",
            json={
                "text": "Amora, fala comigo.",
                "remote_processing_allowed": False,
                "test_mode": True,
            },
            headers=headers,
        )
        assert response.status_code == 200
        reply = response.json()["reply_queued"]
        assert reply is not None
        assert reply["device_id"] == "WEB_SAFARI"

        safari = client.get("/v1/conversation/next?device_id=WEB_SAFARI", headers=headers)
        assert safari.status_code == 200
        assert safari.json()["utterance"] is not None
        assert safari.json()["utterance"]["device_id"] == "WEB_SAFARI"

        android = client.get("/v1/conversation/next?device_id=DEVICE_A15", headers=headers)
        assert android.status_code == 200
        assert android.json()["utterance"] is None
    finally:
        core.close()


def test_web_microphone_reply_is_routed_to_safari_queue(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        core.stt = FakeWebStt()  # type: ignore[assignment]
        response = client.post(
            "/v1/audio/transcribe?language=pt&speaker_id=PERSON_BK&speaker_confidence=0.8&remote_processing_allowed=false&test_mode=true",
            content=b"RIFFweb-routing",
            headers={**headers, "Content-Type": "audio/wav"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["speech_device_id"] == "WEB_SAFARI"
        assert body["reply_queued"] is not None
        assert body["reply_queued"]["device_id"] == "WEB_SAFARI"

        safari = client.get("/v1/conversation/next?device_id=WEB_SAFARI", headers=headers)
        assert safari.status_code == 200
        assert safari.json()["utterance"] is not None
        assert safari.json()["utterance"]["device_id"] == "WEB_SAFARI"
    finally:
        core.close()


def test_android_audio_keeps_android_queue_by_default(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        core.stt = FakeWebStt()  # type: ignore[assignment]
        response = client.post(
            "/v1/audio/transcribe?language=pt&speaker_id=SESSION_ANDROID_SPEAKER_1&speaker_confidence=0.8&remote_processing_allowed=false&test_mode=true",
            content=b"RIFFandroid-routing",
            headers={**headers, "Content-Type": "audio/wav"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["speech_device_id"] == "DEVICE_A15"
        assert body["reply_queued"] is not None
        assert body["reply_queued"]["device_id"] == "DEVICE_A15"
    finally:
        core.close()
