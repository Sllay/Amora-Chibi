from __future__ import annotations

import asyncio
import io
import json
import wave
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from amora.app import AmoraCore
from amora.conversation import UtteranceKind
from amora.events import PrivacyScope
from amora.time import SimulatedClock
from amora.voice.cloud import FISH_FREE_MODEL, FishGeminiVoiceBackend
from amora.voice.local_amora import LocalVoiceUnavailable


class FakeGemini:
    SAMPLE_RATE = 24_000
    model = "fake-gemini-tts"
    voice_id = "Leda"
    configured = True
    available = True

    def __init__(self) -> None:
        self.calls = 0

    def state(self):
        return {"configured": True, "available": True, "cooldown_seconds": 0, "last_error": None}

    async def stream(self, request):
        self.calls += 1
        yield b"\x01\x00" * 2400


def _wav(rate: int = 48_000, frames: int = 4800) -> bytes:
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(b"\x00\x00" * frames)
    return output.getvalue()


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap(clock=SimulatedClock(datetime(2026, 10, 30, 8, 0, tzinfo=UTC)))


def _utterance(core: AmoraCore, *, privacy_scope=PrivacyScope.PERSONAL, remote=True):
    item = asyncio.run(core.conversation.enqueue_text(
        "Oi. Como você está hoje?",
        target_id="PERSON_BK",
        kind=UtteranceKind.DIALOGUE,
        priority=70,
        privacy_scope=privacy_scope,
        source_event_id=None,
        dedupe_key=f"cloud-voice-{privacy_scope.value}-{remote}",
        metadata={"speech_device_id": "WEB_SAFARI", "remote_tts_allowed": remote},
        device_id="WEB_SAFARI",
    ))
    assert item is not None
    return item


def test_fish_free_clone_is_primary_and_never_exposes_credentials(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        core.secrets.set("FISH_API_KEY", "fish-test-secret")
        gemini = FakeGemini()

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url == httpx.URL("https://api.fish.audio/v1/tts")
            assert request.headers["authorization"] == "Bearer fish-test-secret"
            assert request.headers["model"] == FISH_FREE_MODEL
            body = json.loads(request.content)
            assert body["reference_id"] == "amora-reference-test"
            assert body["format"] == "wav"
            return httpx.Response(200, content=_wav())

        backend = FishGeminiVoiceBackend(
            core.secrets,
            reference_id="amora-reference-test",
            transport=httpx.MockTransport(handler),
            gemini=gemini,
        )
        audio = backend.render(_utterance(core))
        assert audio.sample_rate == 48_000
        assert audio.duration_seconds == pytest.approx(0.1)
        assert gemini.calls == 0
        state = backend.state()
        assert state["last_provider"] == "fish"
        assert state["last_model"] == "s2.1-pro-free"
        assert "fish-test-secret" not in str(state)
        assert "amora-reference-test" not in str(state)
        backend.close()
    finally:
        core.close()


def test_fish_failure_falls_back_to_gemini_as_wav(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        core.secrets.set("FISH_API_KEY", "fish-test-secret")
        gemini = FakeGemini()

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, text='{"detail":"fair use"}', headers={"Retry-After": "60"})

        backend = FishGeminiVoiceBackend(
            core.secrets,
            reference_id="amora-reference-test",
            transport=httpx.MockTransport(handler),
            gemini=gemini,
        )
        audio = backend.render(_utterance(core))
        assert audio.wav_bytes.startswith(b"RIFF")
        assert audio.sample_rate == 24_000
        assert gemini.calls == 1
        state = backend.state()
        assert state["last_provider"] == "gemini"
        assert state["last_model"] == "fake-gemini-tts"
        assert "FISH_HTTP_429" in str(state["last_error"])
        backend.close()
    finally:
        core.close()


def test_cloud_voice_requires_remote_tts_permission_and_safe_scope(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        core.secrets.set("FISH_API_KEY", "fish-test-secret")
        backend = FishGeminiVoiceBackend(
            core.secrets,
            reference_id="amora-reference-test",
            transport=httpx.MockTransport(lambda request: httpx.Response(200, content=_wav())),
            gemini=FakeGemini(),
        )
        with pytest.raises(LocalVoiceUnavailable, match="VOICE_REMOTE_TTS_NOT_ALLOWED"):
            backend.render(_utterance(core, remote=False))
        with pytest.raises(LocalVoiceUnavailable, match="VOICE_PRIVACY_DENIED"):
            backend.render(_utterance(core, privacy_scope=PrivacyScope.PRIVATE, remote=True))
        backend.close()
    finally:
        core.close()
