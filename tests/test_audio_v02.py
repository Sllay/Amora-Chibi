import struct

import httpx
import pytest

from amora.audio import (
    AudioSegment,
    EnergyVad,
    GroqWhisperSTT,
    SessionSpeakerTracker,
    SttCredentialsError,
)
from amora.secrets import SecretStore


def test_energy_vad_silence_and_signal() -> None:
    vad = EnergyVad(rms_threshold=500)
    assert vad.detect_pcm16(b"\x00\x00" * 160, sample_rate_hz=16000).speech is False

    pcm = b"".join(
        struct.pack("<h", 2000 if index % 2 else -2000)
        for index in range(160)
    )
    assert vad.detect_pcm16(pcm, sample_rate_hz=16000).speech is True


def test_session_speaker_labels_are_stable_and_distinct() -> None:
    tracker = SessionSpeakerTracker("SESSION_X")
    first = tracker.resolve(diarization_label="A", confidence=0.8)
    first_again = tracker.resolve(diarization_label="A", confidence=0.9)
    second = tracker.resolve(diarization_label="B", confidence=0.8)
    assert first.speaker_id == first_again.speaker_id
    assert first.speaker_id != second.speaker_id


def test_known_voice_is_context_not_authentication() -> None:
    tracker = SessionSpeakerTracker("SESSION_X")
    result = tracker.resolve(known_person_id="PERSON_BK", confidence=0.87)
    assert result.speaker_id == "PERSON_BK"
    assert "KNOWN_PERSON_MATCH_CONTEXT_ONLY" in result.reason_codes


@pytest.mark.asyncio
async def test_groq_stt_uses_verbose_quality_metadata_and_context_prompt(tmp_path) -> None:
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFFfake")
    secrets = SecretStore(tmp_path / "secrets")
    secrets.set("GROQ_API_KEY", "super-secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == "https://api.groq.com/openai/v1/audio/transcriptions"
        assert request.headers["authorization"] == "Bearer super-secret"
        body = await request.aread()
        assert b"whisper-large-v3-turbo" in body
        assert b"verbose_json" in body
        assert b"Amora" in body
        return httpx.Response(
            200,
            json={
                "text": "oi amora",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 1.2,
                        "avg_logprob": -0.12,
                        "no_speech_prob": 0.02,
                    }
                ],
                "x_groq": {"id": "req_1"},
            },
            headers={"x-ratelimit-remaining-requests": "1999"},
        )

    stt = GroqWhisperSTT(secrets, transport=httpx.MockTransport(handler))
    transcript = await stt.transcribe(AudioSegment(path=audio, language="pt"))
    assert transcript.text == "oi amora"
    assert transcript.provider == "groq"
    assert transcript.request_id == "req_1"
    assert transcript.provider_metadata["x-ratelimit-remaining-requests"] == "1999"
    assert transcript.provider_metadata["speech_rejected"] == "false"
    assert transcript.confidence is not None and transcript.confidence > 0.8


@pytest.mark.asyncio
async def test_groq_stt_rejects_noise_hallucination_before_conversation(tmp_path) -> None:
    audio = tmp_path / "fan.wav"
    audio.write_bytes(b"RIFFfake")
    secrets = SecretStore(tmp_path / "secrets")
    secrets.set("GROQ_API_KEY", "super-secret")

    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "text": "Obrigado.",
                "segments": [
                    {
                        "start": 0.0,
                        "end": 0.9,
                        "avg_logprob": -1.05,
                        "no_speech_prob": 0.83,
                    }
                ],
                "x_groq": {"id": "req_noise"},
            },
        )

    transcript = await GroqWhisperSTT(
        secrets,
        transport=httpx.MockTransport(handler),
    ).transcribe(AudioSegment(path=audio, language="pt"))
    assert transcript.text == ""
    assert transcript.provider_metadata["speech_rejected"] == "true"
    assert transcript.provider_metadata["rejection_reason"] == "HIGH_NO_SPEECH_PROBABILITY"


@pytest.mark.asyncio
async def test_groq_stt_requires_secret(tmp_path) -> None:
    audio = tmp_path / "sample.wav"
    audio.write_bytes(b"RIFFfake")
    with pytest.raises(SttCredentialsError):
        await GroqWhisperSTT(SecretStore(tmp_path / "secrets")).transcribe(
            AudioSegment(path=audio)
        )
