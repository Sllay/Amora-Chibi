from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.audio import Transcript
from amora.time import SimulatedClock


class _FakeStt:
    async def transcribe(self, segment):
        return Transcript(
            text="Amora, para um pouquinho.",
            provider="fake",
            model="fake-stt",
            language=segment.language,
            confidence=0.94,
            request_id="req-web-safari",
        )


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 21, 12, 0, tzinfo=UTC))


def _client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    client = TestClient(create_api(core))
    token = core.secrets.get("LOCAL_API_TOKEN")
    assert token
    return core, client, {"Authorization": f"Bearer {token}"}


def test_safari_lab_static_shell_and_manifest_are_served(tmp_path: Path, monkeypatch) -> None:
    core, client, _ = _client(tmp_path, monkeypatch)
    try:
        page = client.get("/web/")
        assert page.status_code == 200
        assert "AMORA · SAFARI LAB" in page.text
        assert "Parar fala" in page.text

        manifest = client.get("/web/manifest.webmanifest")
        assert manifest.status_code == 200
        payload = manifest.json()
        assert payload["short_name"] == "Amora"
        assert payload["start_url"] == "/web/"
        assert payload["display"] == "standalone"
    finally:
        core.close()


def test_web_state_is_authenticated_and_labels_debug_voice_noncanonical(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        assert client.get("/v1/web/state").status_code == 401
        response = client.get("/v1/web/state", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["surface"] == "AMORA_WEB_SAFARI_LAB_V1"
        assert payload["status"] == "experimental"
        assert payload["canonical_voice"] is False
        assert payload["voice_transport"] == "browser_speech_synthesis_debug"
    finally:
        core.close()


def test_web_text_turn_uses_core_pipeline_and_defaults_to_test_isolation(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        payload = {
            "text": "Amora, oi.",
            "remote_processing_allowed": False,
            "test_mode": True,
        }
        assert client.post("/v1/web/utterance", json=payload).status_code == 401

        response = client.post("/v1/web/utterance", json=payload, headers=headers)
        assert response.status_code == 200
        body = response.json()
        assert body["test_mode"] is True
        assert body["observation"]["speaker_id"] == "PERSON_BK"
        assert body["observation"]["addressee"]["entity_id"] == "ENTITY_AMORA"
        assert body["turn"]["state"] == "TAKE_TURN"
    finally:
        core.close()


def test_web_mic_can_request_same_test_isolation_through_shared_audio_contract(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        core.stt = _FakeStt()  # type: ignore[assignment]
        response = client.post(
            "/v1/audio/transcribe?language=pt&speaker_id=PERSON_BK&speaker_confidence=0.8&remote_processing_allowed=false&test_mode=true",
            content=b"RIFFweb-lab-probe",
            headers={**headers, "Content-Type": "audio/wav"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["accepted"] is True
        assert body["test_mode"] is True
        assert body["transcript"]["text"] == "Amora, para um pouquinho."
        assert body["observation"]["speaker_id"] == "PERSON_BK"
        assert body["observation"]["addressee"]["entity_id"] == "ENTITY_AMORA"
        assert body["turn"]["state"] == "TAKE_TURN"
    finally:
        core.close()


def test_web_assets_do_not_enable_stale_service_worker_cache(tmp_path: Path, monkeypatch) -> None:
    core, client, _ = _client(tmp_path, monkeypatch)
    try:
        app = client.get("/web/app.js")
        assert app.status_code == 200
        assert "serviceWorker.register" not in app.text
        assert "web_barge_in" in app.text
        assert "MAX_SPEECH_QUEUE = 3" in app.text
        assert 'test_mode: testToggle.checked ? "true" : "false"' in app.text
    finally:
        core.close()
