from __future__ import annotations

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
    output, ACIR, event payloads, or logs.
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
            "response_format": "json",
            "temperature": "0",
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

        return Transcript(
            text=str(payload.get("text", "")).strip(),
            provider="groq",
            model=self.model,
            language=segment.language,
            request_id=request_id,
            provider_metadata=metadata,
        )
