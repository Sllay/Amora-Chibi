from __future__ import annotations

import asyncio
import io
import os
import threading
import wave
from time import perf_counter
from typing import Any
from uuid import UUID

import httpx

from amora.conversation import SpeechUtterance
from amora.events import PrivacyScope
from amora.secrets import SecretStore

from .local_amora import LocalVoiceAudio, LocalVoiceUnavailable
from .tts import GeminiStreamingTTS, VoiceEngine, VoiceRenderRequest, VoiceUnavailable


FISH_API_URL = "https://api.fish.audio/v1/tts"
FISH_FREE_MODEL = "s2.1-pro-free"
AMORA_CLOUD_VOICE_ID = "AMORA_GOLD_REFERENCE_V1"
CLOUD_AUDIO_POSTPROCESSING = "none"
MAX_CLOUD_AUDIO_BYTES = 16 * 1024 * 1024


class _ProviderFailure(RuntimeError):
    pass


class FishGeminiVoiceBackend:
    """Web voice backend: Fish free clone first, Gemini only as explicit fallback.

    The Fish model is intentionally fixed to ``s2.1-pro-free``. There is no
    environment override to a paid Fish model: if the free endpoint stops
    working, the backend falls through to Gemini (when configured) or fails
    closed so text delivery can continue without silently enabling billing.

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
            "last_provider": last_provider,
            "last_model": last_model,
            "last_error": last_error,
            "sample_rate": last_sample_rate,
        }

    def render(self, item: SpeechUtterance) -> LocalVoiceAudio:
        if item.privacy_scope not in {PrivacyScope.PUBLIC, PrivacyScope.PERSONAL}:
            raise LocalVoiceUnavailable("VOICE_PRIVACY_DENIED")
        if item.metadata.get("remote_tts_allowed") is not True:
            raise LocalVoiceUnavailable("VOICE_REMOTE_TTS_NOT_ALLOWED")
        if not item.text.strip():
            raise LocalVoiceUnavailable("VOICE_EMPTY_TEXT")

        fish_error: str | None = None
        if self.fish_configured:
            try:
                audio = self._render_fish(item.text)
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
