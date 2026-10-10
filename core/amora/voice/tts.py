from __future__ import annotations

import base64
import json
import os
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
    pass


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

    @property
    def available(self) -> bool:
        return self.secrets.get("GEMINI_API_KEY") is not None

    async def stream(self, request: VoiceRenderRequest) -> AsyncIterator[bytes]:
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
        async with httpx.AsyncClient(timeout=timeout) as client:
            async with client.stream(
                "POST",
                url,
                headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                json=payload,
            ) as response:
                if response.status_code >= 400:
                    body = (await response.aread()).decode("utf-8", errors="replace")[:800]
                    raise VoiceUnavailable(f"Gemini TTS failed ({response.status_code}): {body}")
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
        return {
            "expressive_tts_provider": "gemini" if self.provider.available else "none",
            "expressive_tts_model": self.provider.model,
            "expressive_tts_voice": self.provider.voice_id,
            "expressive_tts_sample_rate": self.provider.SAMPLE_RATE,
            "expressive_tts_configured": self.provider.available,
        }

    def prepare(self, utterance_id: UUID | str) -> VoiceRenderRequest:
        item = self.conversation.get_utterance(utterance_id)
        if item is None:
            raise KeyError(str(utterance_id))
        if not self.provider.available:
            raise VoiceUnavailable("expressive TTS is not configured")
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
            self.cognition.record_usage(
                provider="gemini",
                model=request.model,
                task_class="tts",
                success=False,
                turn_id=request.turn_id,
                latency_ms=(perf_counter() - started) * 1000.0,
                metadata={"error_code": type(exc).__name__},
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
