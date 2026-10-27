"""Public render identity, independently pinned by the service client."""

from dataclasses import dataclass
import re


@dataclass(frozen=True, slots=True)
class WorkerVoiceIdentity:
    renderer: str
    model: str
    voice: str
    sample_rate: int = 24000
    streaming_capable: bool = False

    def __post_init__(self):
        for value in (self.renderer, self.model, self.voice):
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_./:@+-]{0,159}", value):
                raise ValueError("VOICE_IDENTITY_INVALID")
        if self.sample_rate != 24000 or type(self.streaming_capable) is not bool:
            raise ValueError("VOICE_IDENTITY_INVALID")

    def headers(self) -> dict[str, str]:
        return {
            "X-Amora-Voice-Renderer": self.renderer,
            "X-Amora-Voice-Model": self.model,
            "X-Amora-TTS-Model": self.model,
            "X-Amora-Voice": self.voice,
            "X-Amora-Sample-Rate": str(self.sample_rate),
            "X-Amora-Streaming-Capable": str(self.streaming_capable).lower(),
        }
