from __future__ import annotations

import math
from pathlib import Path

import httpx

from amora.secrets import SecretStore
from .models import AudioSegment, Transcript


class SttError(RuntimeError):
    pass


class SttCredentialsError(SttError):
    pass


class SttRateLimitedError(SttError):
    def __init__(self, message: str, *, retry_after: str | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class GroqWhisperSTT:
    """Groq/OpenAI-compatible Whisper transcription adapter.

    The API key is obtained only from SecretStore and is never placed in model
    output, ACIR, event payloads, or logs. Verbose segment metadata is used as a
    second no-speech gate so fan/room noise cannot silently become dialogue.
    """

    endpoint = "https://api.groq.com/openai/v1/audio/transcriptions"

    def __init__(
        self,
        secrets: SecretStore,
        *,
        model: str = "whisper-large-v3-turbo",
        timeout_seconds: float = 30.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.secrets = secrets
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    async def transcribe(self, segment: AudioSegment) -> Transcript:
        api_key = self.secrets.get("GROQ_API_KEY")
        if not api_key:
            raise SttCredentialsError("GROQ_API_KEY is not configured")

        path = Path(segment.path)
        if not path.is_file():
            raise FileNotFoundError(path)

        headers = {"Authorization": f"Bearer {api_key}"}
        data = {
            "model": self.model,
            "response_format": "verbose_json",
            "temperature": "0",
            # This is spelling/context guidance only; it does not instruct the STT
            # to fabricate speech or answer on Amora's behalf.
            "prompt": "Conversa casual em português brasileiro. O nome próprio Amora pode aparecer como vocativo.",
        }
        if segment.language:
            data["language"] = segment.language

        async with httpx.AsyncClient(
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            with path.open("rb") as audio_file:
                response = await client.post(
                    self.endpoint,
                    headers=headers,
                    data=data,
                    files={"file": (path.name, audio_file, segment.mime_type)},
                )

        if response.status_code == 429:
            raise SttRateLimitedError(
                "Groq STT rate limit exceeded",
                retry_after=response.headers.get("retry-after"),
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise SttError(f"Groq STT failed with HTTP {response.status_code}") from exc

        payload = response.json()
        raw_text = str(payload.get("text", "")).strip()
        metadata: dict[str, str] = {}
        for key in (
            "x-ratelimit-remaining-requests",
            "x-ratelimit-reset-requests",
            "retry-after",
        ):
            if key in response.headers:
                metadata[key] = response.headers[key]

        request_id = response.headers.get("x-request-id")
        if not request_id and isinstance(payload.get("x_groq"), dict):
            request_id = payload["x_groq"].get("id")

        segments = [item for item in payload.get("segments", []) if isinstance(item, dict)]
        avg_logprob: float | None = None
        no_speech_prob: float | None = None
        if segments:
            weighted_logprob = 0.0
            weighted_no_speech = 0.0
            total_weight = 0.0
            for item in segments:
                try:
                    start = float(item.get("start", 0.0))
                    end = float(item.get("end", start))
                    weight = max(0.05, end - start)
                    logprob = float(item.get("avg_logprob", -1.0))
                    no_speech = float(item.get("no_speech_prob", 0.0))
                except (TypeError, ValueError):
                    continue
                weighted_logprob += logprob * weight
                weighted_no_speech += no_speech * weight
                total_weight += weight
            if total_weight > 0:
                avg_logprob = weighted_logprob / total_weight
                no_speech_prob = weighted_no_speech / total_weight

        rejected = False
        rejection_reason: str | None = None
        word_count = len(raw_text.split())
        if not raw_text:
            rejected = True
            rejection_reason = "EMPTY_TRANSCRIPT"
        elif no_speech_prob is not None and no_speech_prob >= 0.60:
            rejected = True
            rejection_reason = "HIGH_NO_SPEECH_PROBABILITY"
        elif (
            no_speech_prob is not None
            and avg_logprob is not None
            and no_speech_prob >= 0.35
            and avg_logprob <= -0.75
        ):
            rejected = True
            rejection_reason = "LOW_CONFIDENCE_NOISY_SEGMENT"
        elif (
            word_count <= 2
            and no_speech_prob is not None
            and no_speech_prob >= 0.45
        ):
            rejected = True
            rejection_reason = "SHORT_LOW_EVIDENCE_TRANSCRIPT"

        confidence = None
        if avg_logprob is not None:
            confidence = max(0.0, min(1.0, math.exp(avg_logprob)))
            metadata["avg_logprob"] = f"{avg_logprob:.6f}"
        if no_speech_prob is not None:
            metadata["no_speech_prob"] = f"{no_speech_prob:.6f}"
        metadata["speech_rejected"] = "true" if rejected else "false"
        if rejection_reason:
            metadata["rejection_reason"] = rejection_reason

        return Transcript(
            text="" if rejected else raw_text,
            provider="groq",
            model=self.model,
            language=segment.language,
            confidence=confidence,
            request_id=request_id,
            provider_metadata=metadata,
        )
