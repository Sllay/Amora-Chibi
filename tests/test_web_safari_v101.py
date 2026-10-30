from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.audio import Transcript
from amora.conversation import UtteranceKind
from amora.events import PrivacyScope
from amora.time import SimulatedClock
from amora.voice.backend import VoiceChunk
from amora.voice.local_amora import LocalVoiceAudio, LocalVoiceUnavailable


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
    app = create_api(core)
    client = TestClient(app)
    token = core.secrets.get("LOCAL_API_TOKEN")
    assert token
    return core, client, {"Authorization": f"Bearer {token}"}


def _enqueue_web(core: AmoraCore, text: str, dedupe_key: str):
    item = asyncio.run(
        core.conversation.enqueue_text(
            text,
            target_id="PERSON_BK",
            kind=UtteranceKind.DIALOGUE,
            priority=70,
            privacy_scope=PrivacyScope.PERSONAL,
            source_event_id=None,
            dedupe_key=dedupe_key,
            metadata={"speech_device_id": "WEB_SAFARI", "remote_tts_allowed": True},
            device_id="WEB_SAFARI",
        )
    )
    assert item is not None
    return item


def test_safari_lab_static_shell_and_manifest_are_served(tmp_path: Path, monkeypatch) -> None:
    core, client, _ = _client(tmp_path, monkeypatch)
    try:
        page = client.get("/web/")
        assert page.status_code == 200
        assert "AMORA · SAFARI LAB" in page.text
        assert "Parar fala" in page.text
        assert "app-v2.js" in page.text
        assert "Safari não sintetiza mais a voz" in page.text

        manifest = client.get("/web/manifest.webmanifest")
        assert manifest.status_code == 200
        payload = manifest.json()
        assert payload["short_name"] == "Amora"
        assert payload["start_url"] == "/web/"
        assert payload["display"] == "standalone"
    finally:
        core.close()


def test_web_state_is_authenticated_and_exposes_cloud_voice_chain(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_SECRET_FISH_API_KEY", "test-only-fish-key")
    monkeypatch.setenv("AMORA_FISH_REFERENCE_ID", "test-amora-reference")
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        assert client.get("/v1/web/state").status_code == 401
        response = client.get("/v1/web/state", headers=headers)
        assert response.status_code == 200
        payload = response.json()
        assert payload["surface"] == "AMORA_WEB_SAFARI_LAB_V2"
        assert payload["status"] == "experimental"
        assert payload["canonical_voice"] is False
        assert payload["voice_transport"] == "fish_s2_1_pro_free_then_gemini"
        assert payload["voice_candidate"] == "AMORA_GOLD_REFERENCE_V1"
        assert payload["voice"]["primary_provider"] == "fish"
        assert payload["voice"]["fallback_provider"] == "gemini"
        assert payload["voice"]["fish_model"] == "s2.1-pro-free"
        assert payload["voice"]["fish_configured"] is True
        assert payload["voice"]["provider_audio_passthrough"] is True
        assert payload["voice"]["audio_postprocessing"] == "none"
        assert payload["voice"]["streaming"] is True
        assert payload["voice"]["stream_format"] == "pcm_s16le"
        assert payload["voice"]["fish_latency"] == "balanced"
        assert "test-amora-reference" not in str(payload)
        assert "test-only-fish-key" not in str(payload)
    finally:
        core.close()


def test_web_text_turn_uses_core_pipeline_and_routes_reply_to_safari(tmp_path: Path, monkeypatch) -> None:
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
        assert body["reply_queued"] is not None
        assert body["reply_queued"]["device_id"] == "WEB_SAFARI"
    finally:
        core.close()


def test_web_mic_can_request_same_test_isolation_through_shared_audio_contract(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        core.stt = _FakeStt()  # type: ignore[assignment]
        response = client.post(
            "/v1/audio/transcribe?language=pt&speaker_id=PERSON_BK&speaker_confidence=0.8&speech_device_id=WEB_SAFARI&remote_processing_allowed=false&test_mode=true",
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
        assert body["reply_queued"] is not None
        assert body["reply_queued"]["device_id"] == "WEB_SAFARI"
    finally:
        core.close()


def test_web_cloud_voice_route_returns_wav_without_falsifying_delivery(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        item = _enqueue_web(core, "Oi, BK.", "web-cloud-voice-route")
        claimed = core.conversation.claim_next("WEB_SAFARI")
        assert claimed is not None

        renderer = client.app.state.web_voice
        monkeypatch.setattr(
            renderer,
            "render",
            lambda utterance: LocalVoiceAudio(
                wav_bytes=b"RIFFfake-wav",
                sample_rate=48_000,
                sample_count=96_000,
                synthesis_seconds=0.4,
                world_seconds=0.0,
            ),
        )
        monkeypatch.setattr(
            renderer,
            "state",
            lambda: {
                "provider": "fish_s2_1_pro_free_then_gemini",
                "available": True,
                "primary_provider": "fish",
                "fallback_provider": "gemini",
                "last_provider": "fish",
                "last_model": "s2.1-pro-free",
                "voice": "AMORA_GOLD_REFERENCE_V1",
                "provider_audio_passthrough": True,
                "audio_postprocessing": "none",
            },
        )
        response = client.get(f"/v1/web/voice/utterances/{item.id}/audio", headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("audio/wav")
        assert response.headers["x-amora-tts-provider"] == "fish"
        assert response.headers["x-amora-tts-model"] == "s2.1-pro-free"
        assert response.headers["x-amora-voice"] == "AMORA_GOLD_REFERENCE_V1"
        assert response.headers["x-amora-audio-postprocessing"] == "none"
        assert response.headers["x-amora-rtf"] == "0.200000"
        assert response.content == b"RIFFfake-wav"

        unchanged = core.conversation.store.get_utterance(item.id)
        assert unchanged is not None
        assert unchanged.status.value == "claimed"
    finally:
        core.close()


def test_web_cloud_stream_route_proxies_pcm_without_falsifying_delivery(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        item = _enqueue_web(core, "Stream, BK.", "web-cloud-stream-route")
        claimed = core.conversation.claim_next("WEB_SAFARI")
        assert claimed is not None
        renderer = client.app.state.web_voice

        def fake_stream(utterance, session):
            assert utterance.id == item.id
            renderer._remember(provider="fish", model="s2.1-pro-free", error=None, sample_rate=44_100)
            yield VoiceChunk(session, 0, b"\x01\x00" * 128, 44_100, 1)
            yield VoiceChunk(session, 1, b"\x02\x00" * 64, 44_100, 1)
            yield VoiceChunk(session, 2, b"", 44_100, 1, final=True)

        monkeypatch.setattr(renderer, "stream", fake_stream)
        response = client.get(f"/v1/web/voice/utterances/{item.id}/stream", headers=headers)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("application/octet-stream")
        assert response.headers["x-amora-streaming"] == "1"
        assert response.headers["x-amora-audio-format"] == "pcm_s16le"
        assert response.headers["x-amora-sample-rate"] == "44100"
        assert response.headers["x-amora-channels"] == "1"
        assert response.headers["x-amora-tts-provider"] == "fish"
        assert response.headers["x-amora-audio-postprocessing"] == "none"
        assert response.headers["x-accel-buffering"] == "no"
        assert response.content == b"\x01\x00" * 128 + b"\x02\x00" * 64

        unchanged = core.conversation.store.get_utterance(item.id)
        assert unchanged is not None
        assert unchanged.status.value == "claimed"
    finally:
        core.close()


def test_web_delivery_ack_is_only_recorded_after_explicit_playback_ack(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        item = _enqueue_web(core, "ACK só depois do áudio.", "web-delivery-ack")
        claim = client.get("/v1/conversation/next?device_id=WEB_SAFARI", headers=headers)
        assert claim.status_code == 200
        assert claim.json()["utterance"]["id"] == str(item.id)
        assert claim.json()["utterance"]["status"] == "claimed"

        started = client.post(f"/v1/conversation/utterances/{item.id}/started", json={}, headers=headers)
        assert started.status_code == 200
        assert started.json()["utterance"]["status"] == "claimed"
        before_ack = core.conversation.store.get_utterance(item.id)
        assert before_ack is not None
        assert before_ack.status.value == "claimed"

        ack = client.post(
            f"/v1/conversation/utterances/{item.id}/ack",
            json={"spoken": True},
            headers=headers,
        )
        assert ack.status_code == 200
        assert ack.json()["utterance"]["status"] == "spoken"
        after_ack = core.conversation.store.get_utterance(item.id)
        assert after_ack is not None
        assert after_ack.status.value == "spoken"
    finally:
        core.close()


def test_web_manual_cancel_marks_claimed_utterance_cancelled(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        item = _enqueue_web(core, "Essa fala será interrompida.", "web-manual-cancel")
        claim = client.get("/v1/conversation/next?device_id=WEB_SAFARI", headers=headers)
        assert claim.status_code == 200
        assert claim.json()["utterance"]["id"] == str(item.id)

        cancelled = client.post(
            f"/v1/conversation/utterances/{item.id}/cancel",
            json={"reason": "web_manual_stop"},
            headers=headers,
        )
        assert cancelled.status_code == 200
        body = cancelled.json()
        assert body["utterance"]["status"] == "cancelled"
        assert body["cancelled_utterances"] >= 1

        stored = core.conversation.store.get_utterance(item.id)
        assert stored is not None
        assert stored.status.value == "cancelled"
    finally:
        core.close()


def test_web_cloud_voice_failure_cancels_cleanly_and_next_message_can_continue(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        first = _enqueue_web(core, "Primeira fala.", "web-cloud-voice-unavailable")
        claim = client.get("/v1/conversation/next?device_id=WEB_SAFARI", headers=headers)
        assert claim.status_code == 200
        assert claim.json()["utterance"]["id"] == str(first.id)

        renderer = client.app.state.web_voice

        def fail(_):
            raise LocalVoiceUnavailable("VOICE_CLOUD_TTS_UNAVAILABLE")

        monkeypatch.setattr(renderer, "render", fail)
        response = client.get(f"/v1/web/voice/utterances/{first.id}/audio", headers=headers)
        assert response.status_code == 503
        assert response.json()["detail"] == "VOICE_CLOUD_TTS_UNAVAILABLE"

        cancelled = client.post(
            f"/v1/conversation/utterances/{first.id}/cancel",
            json={"reason": "web_cloud_voice_error"},
            headers=headers,
        )
        assert cancelled.status_code == 200
        assert cancelled.json()["utterance"]["status"] == "cancelled"

        second = _enqueue_web(core, "Segunda fala continua.", "web-after-cloud-voice-failure")
        next_response = client.get("/v1/conversation/next?device_id=WEB_SAFARI", headers=headers)
        assert next_response.status_code == 200
        assert next_response.json()["utterance"]["id"] == str(second.id)
        assert next_response.json()["utterance"]["status"] == "claimed"
    finally:
        core.close()


def test_web_v2_has_incremental_pcm_and_no_safari_speech_synthesis_dependency(tmp_path: Path, monkeypatch) -> None:
    core, client, _ = _client(tmp_path, monkeypatch)
    try:
        app = client.get("/web/app-v2.js")
        assert app.status_code == 200
        assert "serviceWorker.register" not in app.text
        assert "speechSynthesis" not in app.text
        assert "SpeechSynthesisUtterance" not in app.text
        assert "/v1/web/voice/utterances/" in app.text
        assert "/stream" in app.text
        assert "response.body.getReader()" in app.text
        assert "new PcmStreamPlayer" in app.text
        assert "pcm16leToFloat32" in app.text
        assert "voicePlayer.prepare(response" in app.text  # WAV compatibility path remains.
        assert 'response.headers.get("x-amora-tts-provider")' in app.text
        assert 'response.headers.get("x-amora-tts-fallback")' in app.text
        assert 'response.headers.get("x-amora-audio-postprocessing")' in app.text
        player = client.get("/web/audio-player.mjs")
        assert player.status_code == 200
        assert "decodeAudioData" in player.text
        assert "source.onended" in player.text
        assert "class PcmStreamPlayer" in player.text
        assert "class WavPlayer" in player.text
        assert "finishStreamedRecord(record)" in app.text
        assert "finishPlayedRecord(record)" in app.text
        assert "/ack" in app.text
        assert "/cancel" in app.text
        assert "failVoiceTransport" in app.text
        assert "web_local_voice_error" in app.text
        assert "web_barge_in" in app.text
        assert "web_manual_stop" in app.text
        assert "MAX_SPEECH_QUEUE = 3" in app.text
        assert 'device_id: "WEB_SAFARI"' in app.text
        assert 'speech_device_id: "WEB_SAFARI"' in app.text
    finally:
        core.close()
