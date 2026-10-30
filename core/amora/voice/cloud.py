from __future__ import annotations

import asyncio
import io
import os
import threading
import wave
from collections.abc import Iterator
from time import perf_counter
from typing import Any
from uuid import UUID

import httpx

from amora.conversation import SpeechUtterance
from amora.events import PrivacyScope
from amora.secrets import SecretStore

from .backend import VoiceChunk, VoiceSession
from .local_amora import LocalVoiceAudio, LocalVoiceUnavailable
from .tts import GeminiStreamingTTS, VoiceEngine, VoiceRenderRequest, VoiceUnavailable


FISH_API_URL = "https://api.fish.audio/v1/tts"
FISH_FREE_MODEL = "s2.1-pro-free"
AMORA_CLOUD_VOICE_ID = "AMORA_GOLD_REFERENCE_V1"
CLOUD_AUDIO_POSTPROCESSING = "none"
MAX_CLOUD_AUDIO_BYTES = 16 * 1024 * 1024
FISH_STREAM_SAMPLE_RATE = 44_100
FISH_STREAM_CHUNK_LENGTH = 200
PCM_STREAM_CHUNK_BYTES = 8192

_FISH_EMOTION_TAGS = {
    "joy": "[happy]",
    "gratitude": "[grateful]",
    "relief": "[relieved]",
    "anger": "[angry]",
    "irritation": "[annoyed]",
    "fear": "[worried]",
    "disappointment": "[disappointed]",
}
_FISH_CUE_TAGS = {
    "LAUGHTER": "[chuckling]",
    "SIGH": "[sighing]",
    "SURPRISE": "[surprised]",
    "DISSATISFACTION": "[annoyed]",
}
_FISH_NONVERBAL_TAGS = {
    "soft_laugh": "[chuckling]",
    "suspiro": "[sighing]",
    "suspira": "[sighing]",
}


class _ProviderFailure(RuntimeError):
    pass


class FishGeminiVoiceBackend:
    """Web voice backend: Fish free clone first, Gemini only as explicit fallback.

    Fish is streamed as raw PCM for Web playback. The browser never receives the
    provider key; Core proxies only audio bytes. The old WAV renderer remains for
    compatibility and fallback paths.

    Cloud audio is deliberately provider passthrough. FINAL_B, VoiceSignature,
    WORLD and the legacy local expression DSP remain preserved for explicit
    local/worker backends but are not applied to Fish/Gemini output.
    """

    def __init__(
        self,
        secrets: SecretStore,
        *,
        reference_id: str | None = None,
        transport: httpx.BaseTransport | None = None,
        gemini: Any | None = None,
        timeout_seconds: float = 45.0,
    ) -> None:
        self.secrets = secrets
        self.reference_id = (
            reference_id
            or os.getenv("AMORA_FISH_REFERENCE_ID")
            or self.secrets.get("FISH_REFERENCE_ID")
            or ""
        ).strip()
        self.timeout_seconds = timeout_seconds
        timeout = httpx.Timeout(timeout_seconds, connect=min(15.0, timeout_seconds))
        self._client = httpx.Client(
            transport=transport,
            timeout=timeout,
            follow_redirects=False,
        )
        self._gemini = gemini or GeminiStreamingTTS(secrets)
        self._lock = threading.Lock()
        self._last_provider: str | None = None
        self._last_model: str | None = None
        self._last_error: str | None = None
        self._last_sample_rate: int | None = None

    @property
    def fish_configured(self) -> bool:
        return bool(self.secrets.get("FISH_API_KEY") and self.reference_id)

    @property
    def gemini_configured(self) -> bool:
        return bool(getattr(self._gemini, "configured", False))

    def state(self) -> dict[str, object]:
        with self._lock:
            last_provider = self._last_provider
            last_model = self._last_model
            last_error = self._last_error
            last_sample_rate = self._last_sample_rate
        gemini_state = self._gemini.state() if hasattr(self._gemini, "state") else {}
        return {
            "provider": "fish_s2_1_pro_free_then_gemini",
            "primary_provider": "fish",
            "fallback_provider": "gemini",
            "configured": self.fish_configured or self.gemini_configured,
            "available": self.fish_configured or bool(gemini_state.get("available", self.gemini_configured)),
            "voice": AMORA_CLOUD_VOICE_ID,
            "fish_model": FISH_FREE_MODEL,
            "fish_configured": self.fish_configured,
            "fish_reference_configured": bool(self.reference_id),
            "gemini_configured": self.gemini_configured,
            "provider_audio_passthrough": True,
            "audio_postprocessing": CLOUD_AUDIO_POSTPROCESSING,
            "streaming": self.fish_configured or self.gemini_configured,
            "stream_format": "pcm_s16le",
            "stream_sample_rate": FISH_STREAM_SAMPLE_RATE if self.fish_configured else None,
            "fish_latency": "balanced",
            "fish_expressive_tags": True,
            "last_provider": last_provider,
            "last_model": last_model,
            "last_error": last_error,
            "sample_rate": last_sample_rate,
        }

    def render(self, item: SpeechUtterance) -> LocalVoiceAudio:
        self._validate_item(item)

        fish_error: str | None = None
        if self.fish_configured:
            try:
                audio = self._render_fish(self._fish_text(item))
            except _ProviderFailure as exc:
                fish_error = str(exc)
                self._remember(error=fish_error)
            else:
                self._remember(
                    provider="fish",
                    model=FISH_FREE_MODEL,
                    error=None,
                    sample_rate=audio.sample_rate,
                )
                return audio
        else:
            fish_error = "FISH_NOT_CONFIGURED"

        if self.gemini_configured:
            try:
                audio = self._render_gemini(item)
            except (VoiceUnavailable, _ProviderFailure, RuntimeError) as exc:
                gemini_error = self._bounded_error(exc, "GEMINI_TTS_FAILED")
                self._remember(error=f"fish={fish_error}; gemini={gemini_error}")
                raise LocalVoiceUnavailable("VOICE_CLOUD_TTS_UNAVAILABLE") from exc
            self._remember(
                provider="gemini",
                model=str(getattr(self._gemini, "model", "gemini-tts")),
                error=fish_error,
                sample_rate=audio.sample_rate,
            )
            return audio

        self._remember(error=fish_error or "VOICE_CLOUD_TTS_NOT_CONFIGURED")
        raise LocalVoiceUnavailable("VOICE_CLOUD_TTS_UNAVAILABLE")

    def stream(self, item: SpeechUtterance, session: VoiceSession) -> Iterator[VoiceChunk]:
        """Yield PCM while Fish is still synthesizing the utterance.

        A provider swap is allowed only before the first audio byte. Once Fish has
        started playing, a mid-stream error fails that delivery instead of splicing
        a different Gemini voice into the same sentence.
        """
        self._validate_item(item)
        if session.utterance_id != item.id:
            raise LocalVoiceUnavailable("VOICE_STREAM_IDENTITY_MISMATCH")

        fish_error: str | None = None
        emitted = False
        if self.fish_configured:
            try:
                for chunk in self._stream_fish(item, session):
                    emitted = emitted or bool(chunk.pcm)
                    yield chunk
                return
            except _ProviderFailure as exc:
                fish_error = str(exc)
                self._remember(error=fish_error)
                if emitted:
                    raise LocalVoiceUnavailable("VOICE_CLOUD_STREAM_INTERRUPTED") from exc
        else:
            fish_error = "FISH_NOT_CONFIGURED"

        if self.gemini_configured:
            try:
                yield from self._stream_gemini_fallback(item, session, fish_error=fish_error)
                return
            except (VoiceUnavailable, _ProviderFailure, RuntimeError) as exc:
                gemini_error = self._bounded_error(exc, "GEMINI_TTS_FAILED")
                self._remember(error=f"fish={fish_error}; gemini={gemini_error}")
                raise LocalVoiceUnavailable("VOICE_CLOUD_TTS_UNAVAILABLE") from exc

        self._remember(error=fish_error or "VOICE_CLOUD_TTS_NOT_CONFIGURED")
        raise LocalVoiceUnavailable("VOICE_CLOUD_TTS_UNAVAILABLE")

    def _stream_fish(self, item: SpeechUtterance, session: VoiceSession) -> Iterator[VoiceChunk]:
        key = self.secrets.get("FISH_API_KEY")
        if not key or not self.reference_id:
            raise _ProviderFailure("FISH_NOT_CONFIGURED")

        payload: dict[str, object] = {
            "text": self._fish_text(item),
            "reference_id": self.reference_id,
            "format": "pcm",
            "sample_rate": FISH_STREAM_SAMPLE_RATE,
            "latency": "balanced",
            "chunk_length": FISH_STREAM_CHUNK_LENGTH,
            "normalize": True,
        }
        expression = item.style.expression
        if expression is not None and abs(expression.rate_scale - 1.0) >= 0.005:
            payload["prosody"] = {"speed": round(expression.rate_scale, 3), "volume": 0.0}

        sequence = 0
        total = 0
        pending = bytearray()
        emitted = False
        try:
            with self._client.stream(
                "POST",
                FISH_API_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "model": FISH_FREE_MODEL,
                },
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    body = response.read().decode("utf-8", errors="replace")
                    compact = " ".join(body.split())[:160]
                    suffix = f": {compact}" if compact else ""
                    raise _ProviderFailure(f"FISH_HTTP_{response.status_code}{suffix}")

                for network_chunk in response.iter_bytes():
                    if not network_chunk:
                        continue
                    total += len(network_chunk)
                    if total > MAX_CLOUD_AUDIO_BYTES:
                        raise _ProviderFailure("FISH_AUDIO_TOO_LARGE")
                    pending.extend(network_chunk)
                    while len(pending) >= PCM_STREAM_CHUNK_BYTES:
                        pcm = bytes(pending[:PCM_STREAM_CHUNK_BYTES])
                        del pending[:PCM_STREAM_CHUNK_BYTES]
                        if not emitted:
                            self._remember(
                                provider="fish",
                                model=FISH_FREE_MODEL,
                                error=None,
                                sample_rate=FISH_STREAM_SAMPLE_RATE,
                            )
                        emitted = True
                        yield VoiceChunk(session, sequence, pcm, FISH_STREAM_SAMPLE_RATE, 1)
                        sequence += 1

                if len(pending) % 2:
                    raise _ProviderFailure("FISH_INVALID_PCM")
                if pending:
                    if not emitted:
                        self._remember(
                            provider="fish",
                            model=FISH_FREE_MODEL,
                            error=None,
                            sample_rate=FISH_STREAM_SAMPLE_RATE,
                        )
                    emitted = True
                    yield VoiceChunk(session, sequence, bytes(pending), FISH_STREAM_SAMPLE_RATE, 1)
                    sequence += 1
        except httpx.HTTPError as exc:
            raise _ProviderFailure(f"FISH_TRANSPORT_{type(exc).__name__}") from exc

        if not emitted:
            raise _ProviderFailure("FISH_EMPTY_AUDIO")
        yield VoiceChunk(session, sequence, b"", FISH_STREAM_SAMPLE_RATE, 1, final=True)

    def _stream_gemini_fallback(
        self,
        item: SpeechUtterance,
        session: VoiceSession,
        *,
        fish_error: str | None,
    ) -> Iterator[VoiceChunk]:
        audio = self._render_gemini(item)
        try:
            with wave.open(io.BytesIO(audio.wav_bytes), "rb") as wav:
                if wav.getnchannels() != 1 or wav.getsampwidth() != 2 or wav.getcomptype() != "NONE":
                    raise _ProviderFailure("GEMINI_INVALID_PCM")
                pcm = wav.readframes(wav.getnframes())
        except wave.Error as exc:
            raise _ProviderFailure("GEMINI_INVALID_PCM") from exc
        if not pcm:
            raise _ProviderFailure("GEMINI_EMPTY_AUDIO")

        model = str(getattr(self._gemini, "model", "gemini-tts"))
        self._remember(
            provider="gemini",
            model=model,
            error=fish_error,
            sample_rate=audio.sample_rate,
        )
        sequence = 0
        for offset in range(0, len(pcm), PCM_STREAM_CHUNK_BYTES):
            yield VoiceChunk(
                session,
                sequence,
                pcm[offset : offset + PCM_STREAM_CHUNK_BYTES],
                audio.sample_rate,
                1,
            )
            sequence += 1
        yield VoiceChunk(session, sequence, b"", audio.sample_rate, 1, final=True)

    def _validate_item(self, item: SpeechUtterance) -> None:
        if item.privacy_scope not in {PrivacyScope.PUBLIC, PrivacyScope.PERSONAL}:
            raise LocalVoiceUnavailable("VOICE_PRIVACY_DENIED")
        if item.metadata.get("remote_tts_allowed") is not True:
            raise LocalVoiceUnavailable("VOICE_REMOTE_TTS_NOT_ALLOWED")
        if not item.text.strip():
            raise LocalVoiceUnavailable("VOICE_EMPTY_TEXT")

    def _render_fish(self, text: str) -> LocalVoiceAudio:
        key = self.secrets.get("FISH_API_KEY")
        if not key or not self.reference_id:
            raise _ProviderFailure("FISH_NOT_CONFIGURED")

        started = perf_counter()
        try:
            with self._client.stream(
                "POST",
                FISH_API_URL,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                    "model": FISH_FREE_MODEL,
                },
                json={
                    "text": text,
                    "reference_id": self.reference_id,
                    "format": "wav",
                    "latency": "balanced",
                    "chunk_length": FISH_STREAM_CHUNK_LENGTH,
                },
            ) as response:
                if response.status_code >= 400:
                    body = response.read().decode("utf-8", errors="replace")
                    compact = " ".join(body.split())[:160]
                    suffix = f": {compact}" if compact else ""
                    raise _ProviderFailure(f"FISH_HTTP_{response.status_code}{suffix}")
                body = bytearray()
                for chunk in response.iter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_CLOUD_AUDIO_BYTES:
                        raise _ProviderFailure("FISH_AUDIO_TOO_LARGE")
        except httpx.HTTPError as exc:
            raise _ProviderFailure(f"FISH_TRANSPORT_{type(exc).__name__}") from exc

        wav_bytes = bytes(body)
        sample_rate, frames = self._inspect_wav(wav_bytes, "FISH_INVALID_WAV")
        return LocalVoiceAudio(
            wav_bytes=wav_bytes,
            sample_rate=sample_rate,
            sample_count=frames,
            synthesis_seconds=perf_counter() - started,
            world_seconds=0.0,
        )

    def _render_gemini(self, item: SpeechUtterance) -> LocalVoiceAudio:
        started = perf_counter()
        turn_id = self._turn_id(item)
        request = VoiceRenderRequest(
            utterance_id=item.id,
            turn_id=turn_id,
            text=item.text,
            voice_id=str(getattr(self._gemini, "voice_id", "Leda")),
            model=str(getattr(self._gemini, "model", "gemini-tts")),
            style=VoiceEngine._style_for(item),
            privacy_scope=item.privacy_scope,
        )

        async def collect() -> bytes:
            pcm = bytearray()
            async for chunk in self._gemini.stream(request):
                pcm.extend(chunk)
                if len(pcm) > MAX_CLOUD_AUDIO_BYTES:
                    raise _ProviderFailure("GEMINI_AUDIO_TOO_LARGE")
            if not pcm:
                raise _ProviderFailure("GEMINI_EMPTY_AUDIO")
            return bytes(pcm)

        try:
            pcm = asyncio.run(collect())
        except VoiceUnavailable:
            raise
        except _ProviderFailure:
            raise
        except Exception as exc:
            raise _ProviderFailure(f"GEMINI_TRANSPORT_{type(exc).__name__}") from exc

        sample_rate = int(getattr(self._gemini, "SAMPLE_RATE", 24_000))
        if sample_rate < 8_000 or sample_rate > 192_000 or len(pcm) % 2:
            raise _ProviderFailure("GEMINI_INVALID_PCM")
        output = io.BytesIO()
        with wave.open(output, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(sample_rate)
            wav.writeframes(pcm)
        return LocalVoiceAudio(
            wav_bytes=output.getvalue(),
            sample_rate=sample_rate,
            sample_count=len(pcm) // 2,
            synthesis_seconds=perf_counter() - started,
            world_seconds=0.0,
        )

    @classmethod
    def _fish_text(cls, item: SpeechUtterance) -> str:
        tags: list[str] = []
        expression = item.style.expression
        if (
            expression is not None
            and expression.source_emotion
            and expression.source_intensity >= 0.35
        ):
            tag = _FISH_EMOTION_TAGS.get(str(expression.source_emotion))
            if tag:
                tags.append(tag)

        cues = item.metadata.get("cues", [])
        if isinstance(cues, list):
            for cue in cues:
                tag = _FISH_CUE_TAGS.get(str(cue))
                if tag and tag not in tags:
                    tags.append(tag)
        nonverbal = item.metadata.get("nonverbal_cues", [])
        if isinstance(nonverbal, list):
            for cue in nonverbal:
                tag = _FISH_NONVERBAL_TAGS.get(str(cue).lower())
                if tag and tag not in tags:
                    tags.append(tag)
        return f"{' '.join(tags)} {item.text}".strip()

    @staticmethod
    def _inspect_wav(body: bytes, error_code: str) -> tuple[int, int]:
        if not body or len(body) > MAX_CLOUD_AUDIO_BYTES:
            raise _ProviderFailure(error_code)
        try:
            with wave.open(io.BytesIO(body), "rb") as wav:
                sample_rate = wav.getframerate()
                frames = wav.getnframes()
                channels = wav.getnchannels()
                sample_width = wav.getsampwidth()
                compression = wav.getcomptype()
                if (
                    frames <= 0
                    or not 8_000 <= sample_rate <= 192_000
                    or channels not in {1, 2}
                    or sample_width not in {1, 2, 3, 4}
                    or compression != "NONE"
                ):
                    raise ValueError("unsupported wav")
        except (wave.Error, EOFError, ValueError) as exc:
            raise _ProviderFailure(error_code) from exc
        return sample_rate, frames

    @staticmethod
    def _turn_id(item: SpeechUtterance) -> UUID | None:
        raw = item.metadata.get("cognitive_turn_id")
        if not raw:
            return None
        try:
            return UUID(str(raw))
        except ValueError:
            return None

    @staticmethod
    def _bounded_error(exc: BaseException, fallback: str) -> str:
        text = " ".join(str(exc).split())[:160]
        return text or fallback

    def _remember(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        error: str | None = None,
        sample_rate: int | None = None,
    ) -> None:
        with self._lock:
            if provider is not None:
                self._last_provider = provider
            if model is not None:
                self._last_model = model
            self._last_error = error
            if sample_rate is not None:
                self._last_sample_rate = sample_rate

    def close(self) -> None:
        self._client.close()
