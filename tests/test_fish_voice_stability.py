from __future__ import annotations

import asyncio
import io
import json
import wave
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from amora.app import AmoraCore
from amora.conversation import UtteranceKind
from amora.conversation.models import VoiceExpressionStyle
from amora.events import PrivacyScope
from amora.time import SimulatedClock
from amora.voice.backend import VoiceSession, validated_chunks
from amora.voice.cloud import (
    FISH_EMOTION_TAG_THRESHOLD,
    FISH_MAX_STYLE_TAGS,
    FISH_STREAM_CHUNK_LENGTH,
    FISH_STREAM_SAMPLE_RATE,
    FISH_TEMPERATURE,
    FISH_TOP_P,
    FishGeminiVoiceBackend,
)


class NoGemini:
    configured = False
    available = False

    def state(self):
        return {"configured": False, "available": False}


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
    return AmoraCore.bootstrap(clock=SimulatedClock(datetime(2026, 10, 31, 8, 0, tzinfo=UTC)))


def _utterance(core: AmoraCore):
    item = asyncio.run(
        core.conversation.enqueue_text(
            "Oi. Como você está hoje?",
            target_id="PERSON_BK",
            kind=UtteranceKind.DIALOGUE,
            priority=70,
            privacy_scope=PrivacyScope.PERSONAL,
            source_event_id=None,
            dedupe_key=f"fish-stability-{uuid4()}",
            metadata={"speech_device_id": "WEB_SAFARI", "remote_tts_allowed": True},
            device_id="WEB_SAFARI",
        )
    )
    assert item is not None
    return item


def test_stream_uses_conservative_sampling_bounded_tags_and_records_first_pcm(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        core.secrets.set("FISH_API_KEY", "fish-test-secret")
        pcm = b"\x01\x00" * 5000

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["format"] == "pcm"
            assert body["sample_rate"] == FISH_STREAM_SAMPLE_RATE
            assert body["latency"] == "balanced"
            assert body["chunk_length"] == FISH_STREAM_CHUNK_LENGTH
            assert body["normalize"] is True
            assert body["temperature"] == pytest.approx(FISH_TEMPERATURE)
            assert body["top_p"] == pytest.approx(FISH_TOP_P)
            assert body["prosody"] == {"speed": 1.02, "volume": 0.0}
            assert body["text"].startswith("[happy] [chuckling] ")
            assert "[sighing]" not in body["text"]
            return httpx.Response(200, content=pcm)

        backend = FishGeminiVoiceBackend(
            core.secrets,
            reference_id="amora-reference-test",
            transport=httpx.MockTransport(handler),
            gemini=NoGemini(),
        )
        item = _utterance(core)
        item.style.expression = VoiceExpressionStyle(
            source_emotion="joy",
            source_intensity=0.8,
            rate_scale=1.02,
        )
        item.metadata["cues"] = ["LAUGHTER", "SIGH", "SURPRISE"]
        session = VoiceSession(uuid4(), uuid4(), item.id)

        chunks = list(validated_chunks(session, backend.stream(item, session)))
        assert chunks[0].pcm
        assert chunks[-1].final is True

        state = backend.state()
        assert state["fish_temperature"] == pytest.approx(FISH_TEMPERATURE)
        assert state["fish_top_p"] == pytest.approx(FISH_TOP_P)
        assert state["fish_emotion_tag_threshold"] == pytest.approx(FISH_EMOTION_TAG_THRESHOLD)
        assert state["fish_max_style_tags"] == FISH_MAX_STYLE_TAGS
        assert isinstance(state["fish_headers_ms"], float)
        assert isinstance(state["fish_first_byte_ms"], float)
        assert isinstance(state["fish_first_pcm_ms"], float)
        assert state["fish_headers_ms"] >= 0.0
        assert state["fish_first_byte_ms"] >= state["fish_headers_ms"]
        assert state["fish_first_pcm_ms"] >= state["fish_first_byte_ms"]
        backend.close()
    finally:
        core.close()


def test_emotion_below_threshold_does_not_over_direct_fish(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        core.secrets.set("FISH_API_KEY", "fish-test-secret")
        seen_text = ""

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal seen_text
            body = json.loads(request.content)
            seen_text = body["text"]
            return httpx.Response(200, content=b"\x01\x00" * 5000)

        backend = FishGeminiVoiceBackend(
            core.secrets,
            reference_id="amora-reference-test",
            transport=httpx.MockTransport(handler),
            gemini=NoGemini(),
        )
        item = _utterance(core)
        item.style.expression = VoiceExpressionStyle(
            source_emotion="joy",
            source_intensity=FISH_EMOTION_TAG_THRESHOLD - 0.01,
        )
        item.metadata["cues"] = ["LAUGHTER", "SIGH"]
        session = VoiceSession(uuid4(), uuid4(), item.id)
        list(validated_chunks(session, backend.stream(item, session)))

        assert seen_text.startswith("[chuckling] [sighing] ")
        assert "[happy]" not in seen_text
        backend.close()
    finally:
        core.close()


def test_wav_recovery_uses_same_fish_quality_policy_and_prosody(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        core.secrets.set("FISH_API_KEY", "fish-test-secret")

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            assert body["format"] == "wav"
            assert body["latency"] == "balanced"
            assert body["chunk_length"] == FISH_STREAM_CHUNK_LENGTH
            assert body["normalize"] is True
            assert body["temperature"] == pytest.approx(FISH_TEMPERATURE)
            assert body["top_p"] == pytest.approx(FISH_TOP_P)
            assert body["prosody"] == {"speed": 0.98, "volume": 0.0}
            assert "sample_rate" not in body
            return httpx.Response(200, content=_wav())

        backend = FishGeminiVoiceBackend(
            core.secrets,
            reference_id="amora-reference-test",
            transport=httpx.MockTransport(handler),
            gemini=NoGemini(),
        )
        item = _utterance(core)
        item.style.expression = VoiceExpressionStyle(rate_scale=0.98)
        audio = backend.render(item)

        assert audio.sample_rate == 48_000
        state = backend.state()
        assert state["last_provider"] == "fish"
        assert isinstance(state["fish_headers_ms"], float)
        assert isinstance(state["fish_first_byte_ms"], float)
        assert state["fish_first_pcm_ms"] is None
        backend.close()
    finally:
        core.close()
