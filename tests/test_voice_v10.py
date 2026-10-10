from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.conversation import UtteranceKind, UtteranceStatus
from amora.events import PrivacyScope
from amora.voice.tts import GeminiStreamingTTS, RemoteVoiceNotAllowed, VoiceEngine, VoiceRenderRequest, VoiceUnavailable


class FakeVoiceProvider:
    SAMPLE_RATE = 24_000
    model = "fake-voice-model"
    voice_id = "fake-voice"
    available = True
    configured = True

    def ensure_available(self) -> None:
        return None

    def state(self) -> dict[str, object]:
        return {
            "configured": True,
            "available": True,
            "cooldown_seconds": 0,
            "last_error": None,
        }

    async def stream(self, request: VoiceRenderRequest):
        assert request.text
        yield b"\x01\x00" * 120
        yield b"\x02\x00" * 120


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap()


@pytest.mark.asyncio
async def test_remote_tts_flag_is_explicit_and_cancel_never_requeues(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        decision, first = await core.conversation.observe_completed_utterance(
            "Amora, fala comigo",
            speaker_id="PERSON_BK",
            addressee_id="ENTITY_AMORA",
            source_event_id=uuid4(),
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
        )
        assert decision.state.value == "TAKE_TURN"
        assert first is not None
        assert first.metadata["remote_tts_allowed"] is True
        claimed = core.conversation.claim_next()
        assert claimed is not None
        item, cancelled = await core.conversation.cancel_utterance(claimed.id, reason="barge_in")
        assert item is not None and item.status == UtteranceStatus.CANCELLED
        assert cancelled >= 1
        assert core.conversation.claim_next() is None
    finally:
        core.close()


@pytest.mark.asyncio
async def test_private_scope_never_authorizes_remote_tts(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        decision, first = await core.conversation.observe_completed_utterance(
            "Amora, isso é privado",
            speaker_id="PERSON_BK",
            addressee_id="ENTITY_AMORA",
            source_event_id=uuid4(),
            privacy_scope=PrivacyScope.PRIVATE,
            remote_processing_allowed=True,
        )
        assert decision.state.value == "TAKE_TURN"
        assert first is not None
        assert first.metadata["remote_tts_allowed"] is False
    finally:
        core.close()


def test_voice_engine_enforces_utterance_policy_before_remote_provider(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        item = core.conversation.composer.style_for_mood(core.emotion.current_mood())
        import asyncio
        utterance = asyncio.run(core.conversation.enqueue_text(
            "Isso fica local.",
            target_id="PERSON_BK",
            kind=UtteranceKind.DIALOGUE,
            priority=70,
            privacy_scope=PrivacyScope.PRIVATE,
            source_event_id=None,
            dedupe_key="voice-private",
            metadata={"remote_tts_allowed": True},
            style=item,
        ))
        assert utterance is not None
        engine = VoiceEngine(core.conversation.store, core.cognition.store, FakeVoiceProvider(), core.clock)  # type: ignore[arg-type]
        with pytest.raises(RemoteVoiceNotAllowed):
            engine.prepare(utterance.id)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_voice_engine_streams_pcm_and_records_usage(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        utterance = await core.conversation.enqueue_text(
            "Oi, papis.",
            target_id="PERSON_BK",
            kind=UtteranceKind.DIALOGUE,
            priority=70,
            privacy_scope=PrivacyScope.PERSONAL,
            source_event_id=None,
            dedupe_key="voice-allowed",
            metadata={"remote_tts_allowed": True, "cognitive_turn_id": str(uuid4())},
        )
        assert utterance is not None
        engine = VoiceEngine(core.conversation.store, core.cognition.store, FakeVoiceProvider(), core.clock)  # type: ignore[arg-type]
        request = engine.prepare(utterance.id)
        chunks = [chunk async for chunk in engine.stream(request)]
        assert len(chunks) == 2
        assert sum(map(len, chunks)) == 480
        journal = core.cognition.store.journal_for_turn(request.turn_id)
        assert any(item.source == "voice_engine" for item in journal)
    finally:
        core.close()


def test_voice_api_reports_unconfigured_provider_without_exposing_key(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        token = core.secrets.get("LOCAL_API_TOKEN")
        client = TestClient(create_api(core))
        response = client.get("/v1/voice/state", headers={"Authorization": f"Bearer {token}"})
        assert response.status_code == 200
        body = response.json()
        assert body["expressive_tts_configured"] is False
        assert body["expressive_tts_available"] is False
        assert "api_key" not in str(body).lower()
    finally:
        core.close()


def test_gemini_retry_duration_parses_hours_minutes_and_seconds() -> None:
    assert GeminiStreamingTTS._duration_seconds("2h27m5.5s") == pytest.approx(8825.5)
    assert GeminiStreamingTTS._duration_seconds("90s") == pytest.approx(90.0)
    assert GeminiStreamingTTS._duration_seconds("n/a") is None


def test_voice_api_returns_503_before_stream_headers_when_tts_is_rate_limited(tmp_path: Path, monkeypatch) -> None:
    import asyncio

    core = _core(tmp_path, monkeypatch)
    try:
        core.secrets.set("GEMINI_API_KEY", "test-only-key")
        utterance = asyncio.run(core.conversation.enqueue_text(
            "Teste de fallback local.",
            target_id="PERSON_BK",
            kind=UtteranceKind.DIALOGUE,
            priority=70,
            privacy_scope=PrivacyScope.PERSONAL,
            source_event_id=None,
            dedupe_key="voice-rate-limit",
            metadata={"remote_tts_allowed": True},
        ))
        assert utterance is not None

        async def rate_limited(self, request):
            if False:
                yield b""
            raise VoiceUnavailable("Gemini TTS quota exhausted", retry_after_seconds=123.2)

        monkeypatch.setattr(GeminiStreamingTTS, "stream", rate_limited)
        token = core.secrets.get("LOCAL_API_TOKEN")
        client = TestClient(create_api(core))
        response = client.get(
            f"/v1/voice/utterances/{utterance.id}/audio",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 503
        assert response.headers["retry-after"] == "124"
        assert response.json()["detail"] == "Gemini TTS quota exhausted"
    finally:
        core.close()
