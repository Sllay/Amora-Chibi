from pathlib import Path

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.audio import Transcript


class FakeStt:
    async def transcribe(self, segment):
        assert segment.mime_type == "audio/wav"
        assert segment.path.read_bytes() == b"RIFFfake-wave"
        return Transcript(
            text="Amora, vem cá",
            provider="fake",
            model="fake-stt",
            language=segment.language,
            request_id="req-test-audio",
        )


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap()


def test_raw_wav_endpoint_requires_auth_and_emits_social_observation(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        core.stt = FakeStt()  # type: ignore[assignment]
        token = core.secrets.get("LOCAL_API_TOKEN")
        client = TestClient(create_api(core))
        payload = b"RIFFfake-wave"

        assert client.post(
            "/v1/audio/transcribe",
            content=payload,
            headers={"Content-Type": "audio/wav"},
        ).status_code == 401

        before = core.database.event_count()
        response = client.post(
            "/v1/audio/transcribe?speaker_id=SESSION_ANDROID_SPEAKER_1&speaker_confidence=0.4&language=pt",
            content=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "audio/wav",
            },
        )
        assert response.status_code == 200
        body = response.json()
        assert body["transcript"]["text"] == "Amora, vem cá"
        assert body["observation"]["speaker_id"] == "SESSION_ANDROID_SPEAKER_1"
        assert body["observation"]["addressee"]["entity_id"] == "ENTITY_AMORA"
        assert body["turn"]["state"] == "TAKE_TURN"
        assert body["reply_queued"] is not None
        assert body["reply_queued"]["target_id"] == "SESSION_ANDROID_SPEAKER_1"
        # social observation + speech queued + cognitive response plan
        assert core.database.event_count() == before + 3
        assert core.conversation.store.pending_count() == 1
        assert core.cognition.store.message_count() >= 2
        assert core.cognition.store.journal_count() >= 4
        assert list((core.paths.cache / "incoming").glob("*.wav")) == []
    finally:
        core.close()


def test_raw_wav_endpoint_rejects_wrong_media_type(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        token = core.secrets.get("LOCAL_API_TOKEN")
        client = TestClient(create_api(core))
        response = client.post(
            "/v1/audio/transcribe",
            content=b"not-a-wav",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/octet-stream",
            },
        )
        assert response.status_code == 415
    finally:
        core.close()
