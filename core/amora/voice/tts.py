from __future__ import annotations

import base64
import json
import math
import os
import re
import time
from dataclasses import dataclass
from time import perf_counter
from typing import AsyncIterator
from uuid import UUID

import httpx

from amora.cognition import CognitiveJournalEntry, CognitiveStore, JournalStage
from amora.conversation import ConversationStore, SpeechUtterance
from amora.events import PrivacyScope
from amora.secrets import SecretStore
from amora.time import Clock


class VoiceUnavailable(RuntimeError):
    def __init__(self, message: str, *, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


class RemoteVoiceNotAllowed(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class VoiceRenderRequest:
    utterance_id: UUID
    turn_id: UUID | None
    text: str
    voice_id: str
    model: str
    style: str
    privacy_scope: PrivacyScope


class GeminiStreamingTTS:
    SAMPLE_RATE = 24_000

    def __init__(
        self,
        secrets: SecretStore,
        *,
        model: str | None = None,
        voice_id: str | None = None,
        timeout_seconds: float = 45.0,
    ) -> None:
        self.secrets = secrets
        self.model = model or os.getenv("AMORA_TTS_MODEL", "gemini-3.8-flash-lite-tts")
        self.voice_id = voice_id or os.getenv("AMORA_TTS_VOICE", "Leda")
        self.timeout_seconds = timeout_seconds
        self._cooldown_until = 0.0
        self._last_error: str | None = None

    @property
    def configured(self) -> bool:
        return self.secrets.get("GEMINI_API_KEY") is not None

    @property
    def cooldown_remaining(self) -> float:
        return max(0.0, self._cooldown_until - time.monotonic())

    @property
    def available(self) -> bool:
        return self.configured and self.cooldown_remaining <= 0.0

    def state(self) -> dict[str, object]:
        remaining = self.cooldown_remaining
        return {
            "configured": self.configured,
            "available": self.available,
            "cooldown_seconds": math.ceil(remaining) if remaining > 0 else 0,
            "last_error": self._last_error,
            "model": self.model,
            "voice": self.voice_id,
            "sample_rate": self.SAMPLE_RATE,
        }

    def ensure_available(self) -> None:
        if not self.configured:
            raise VoiceUnavailable("GEMINI_API_KEY is not configured")
        remaining = self.cooldown_remaining
        if remaining > 0:
            raise VoiceUnavailable(
                "Gemini TTS is temporarily rate-limited",
                retry_after_seconds=math.ceil(remaining),
            )

    async def stream(self, request: VoiceRenderRequest) -> AsyncIterator[bytes]:
        self.ensure_available()
        key = self.secrets.get("GEMINI_API_KEY")
        if not key:
            raise VoiceUnavailable("GEMINI_API_KEY is not configured")
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:streamGenerateContent?alt=sse"
        payload = {
            "contents": [{
                "role": "user",
                "parts": [{
                    "text": request.text,
                    "speech_metadata": {"style": request.style},
                }],
            }],
            "generationConfig": {
                "responseModalities": ["AUDIO"],
                "responseFormat": {
                    "audio": {"mimeType": "AUDIO_L16", "sampleRate": self.SAMPLE_RATE},
                },
                "speechConfig": {"voiceConfig": {"voice": request.voice_id}},
            },
        }
        timeout = httpx.Timeout(self.timeout_seconds, connect=15.0)
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
            async with client.stream(
                "POST",
                url,
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:1200]
                    if response.status_code == 429:
                        retry = self._retry_after_seconds(response, body) or 60.0
                        self._cooldown_until = time.monotonic() + retry
                        self._last_error = "quota/rate limit"
                        raise VoiceUnavailable(
                            "Gemini TTS quota exhausted",
                            retry_after_seconds=retry,
                        )
                    self._last_error = f"HTTP {response.status_code}"
                    compact = " ".join(body.split())[:240]
                    raise VoiceUnavailable(f"Gemini TTS failed ({response.status_code}): {compact}")
                self._last_error = None
                async for line in response.aiter_lines():
                    if not line.startswith("data:"):
                        continue
                    raw = line[5:].strip()
                    if not raw or raw == "[DONE]":
                        continue
                    try:
                        obj = json.loads(raw)
                    except json.JSONDecodeError:
                        continue
                    for candidate in obj.get("candidates") or []:
                        content = candidate.get("content") or {}
                        for part in content.get("parts") or []:
                            inline = part.get("inlineData") or part.get("inline_data") or {}
                            data = inline.get("data")
                            if not data:
                                continue
                            try:
                                chunk = base64.b64decode(data, validate=True)
                            except (ValueError, TypeError):
                                continue
                            if chunk:
                                yield chunk

    @classmethod
    def _retry_after_seconds(cls, response: httpx.Response, body: str) -> float | None:
        header = response.headers.get("retry-after")
        if header:
            try:
                return max(0.0, float(header.strip()))
            except ValueError:
                pass
        try:
            root = json.loads(body)
        except json.JSONDecodeError:
            root = {}
        details = ((root.get("error") or {}).get("details") or []) if isinstance(root, dict) else []
        for item in details:
            if not isinstance(item, dict):
                continue
            raw = item.get("retryDelay") or item.get("retry_delay")
            seconds = cls._duration_seconds(str(raw)) if raw else None
            if seconds is not None:
                return seconds
        match = re.search(r"Please retry in\s+([0-9.]+(?:h|m|s)(?:[0-9.]+(?:h|m|s))*)", body, re.IGNORECASE)
        if match:
            return cls._duration_seconds(match.group(1))
        return None

    @staticmethod
    def _duration_seconds(value: str) -> float | None:
        matches = re.findall(r"([0-9]+(?:\.[0-9]+)?)([hms])", value.strip().lower())
        if not matches:
            return None
        factors = {"h": 3600.0, "m": 60.0, "s": 1.0}
        return sum(float(amount) * factors[unit] for amount, unit in matches)


class VoiceEngine:
    """Privacy-gated expressive TTS with local Android fallback by design."""

    def __init__(
        self,
        conversation: ConversationStore,
        cognition: CognitiveStore,
        provider: GeminiStreamingTTS,
        clock: Clock,
    ) -> None:
        self.conversation = conversation
        self.cognition = cognition
        self.provider = provider
        self.clock = clock

    def state(self) -> dict[str, object]:
        provider_state = self.provider.state()
        return {
            "expressive_tts_provider": "gemini" if provider_state["configured"] else "none",
            "expressive_tts_model": self.provider.model,
            "expressive_tts_voice": self.provider.voice_id,
            "expressive_tts_sample_rate": self.provider.SAMPLE_RATE,
            "expressive_tts_configured": provider_state["configured"],
            "expressive_tts_available": provider_state["available"],
            "expressive_tts_cooldown_seconds": provider_state["cooldown_seconds"],
            "expressive_tts_last_error": provider_state["last_error"],
        }

    def prepare(self, utterance_id: UUID | str) -> VoiceRenderRequest:
        item = self.conversation.get_utterance(utterance_id)
        if item is None:
            raise KeyError(str(utterance_id))
        self.provider.ensure_available()
        if item.privacy_scope not in {PrivacyScope.PUBLIC, PrivacyScope.PERSONAL}:
            raise RemoteVoiceNotAllowed("privacy scope requires local TTS")
        if item.metadata.get("remote_tts_allowed") is not True:
            raise RemoteVoiceNotAllowed("utterance did not authorize remote TTS")
        turn_raw = item.metadata.get("cognitive_turn_id")
        turn_id = None
        if turn_raw:
            try:
                turn_id = UUID(str(turn_raw))
            except ValueError:
                turn_id = None
        return VoiceRenderRequest(
            utterance_id=item.id,
            turn_id=turn_id,
            text=item.text,
            voice_id=self.provider.voice_id,
            model=self.provider.model,
            style=self._style_for(item),
            privacy_scope=item.privacy_scope,
        )

    async def stream(self, request: VoiceRenderRequest) -> AsyncIterator[bytes]:
        started = perf_counter()
        byte_count = 0
        if request.turn_id:
            self.cognition.put_journal(CognitiveJournalEntry(
                turn_id=request.turn_id,
                stage=JournalStage.TTS_STARTED,
                timestamp=self.clock.now(),
                source="voice_engine",
                summary="expressive TTS stream started",
                reason_codes=["REMOTE_TTS_ALLOWED", "GEMINI_STREAMING_TTS"],
                payload={"utterance_id": str(request.utterance_id), "voice_id": request.voice_id},
                privacy_scope=request.privacy_scope,
                provider="gemini",
                model=request.model,
            ))
        try:
            async for chunk in self.provider.stream(request):
                byte_count += len(chunk)
                yield chunk
        except Exception as exc:
            metadata: dict[str, object] = {"error_code": type(exc).__name__}
            retry_after = getattr(exc, "retry_after_seconds", None)
            if retry_after is not None:
                metadata["retry_after_seconds"] = retry_after
            self.cognition.record_usage(
                provider="gemini",
                model=request.model,
                task_class="tts",
                success=False,
                turn_id=request.turn_id,
                latency_ms=(perf_counter() - started) * 1000.0,
                metadata=metadata,
            )
            raise
        else:
            elapsed = (perf_counter() - started) * 1000.0
            self.cognition.record_usage(
                provider="gemini",
                model=request.model,
                task_class="tts",
                success=True,
                turn_id=request.turn_id,
                latency_ms=elapsed,
                metadata={"audio_bytes": byte_count, "sample_rate": self.provider.SAMPLE_RATE, "voice_id": request.voice_id},
            )
            if request.turn_id:
                self.cognition.put_journal(CognitiveJournalEntry(
                    turn_id=request.turn_id,
                    stage=JournalStage.TTS_FINISHED,
                    timestamp=self.clock.now(),
                    source="voice_engine",
                    summary="expressive TTS stream delivered",
                    reason_codes=["REMOTE_AUDIO_STREAM_COMPLETE"],
                    payload={"utterance_id": str(request.utterance_id), "audio_bytes": byte_count, "latency_ms": elapsed},
                    privacy_scope=request.privacy_scope,
                    provider="gemini",
                    model=request.model,
                ))

    @staticmethod
    def _style_for(item: SpeechUtterance) -> str:
        base = (
            "Português brasileiro natural. Voz feminina jovem-adulta, calorosa, suave e fofa sem soar infantil; "
            "conversa íntima e espontânea, dicção clara, sem tom de narradora, assistente, anúncio ou leitura. "
            "Use pausas naturais, microvariação, respiração discreta e ritmo humano."
        )
        valence = item.style.valence
        arousal = item.style.arousal
        modifiers: list[str] = []
        if valence > 0.35:
            modifiers.append("Soar mais sorridente e carinhosa.")
        elif valence < -0.35:
            modifiers.append("Soar mais contida e séria, sem dramatização teatral.")
        if arousal > 0.45:
            modifiers.append("Um pouco mais animada e rápida.")
        elif arousal < -0.25:
            modifiers.append("Ritmo ligeiramente mais lento e macio.")
        return " ".join([base, *modifiers])
