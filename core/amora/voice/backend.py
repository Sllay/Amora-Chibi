"""Rendering capability. Delivery/ACK and canonical state remain in the Core."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID
from amora.conversation import SpeechUtterance
from .local_amora import LocalVoiceAudio


class VoiceBackend(Protocol):
    def state(self) -> dict[str, object]: ...
    def render(self, item: SpeechUtterance) -> LocalVoiceAudio: ...


@dataclass(frozen=True, slots=True)
class VoiceSession:
    """Audio transport identity only; no cognitive state or browser credential."""

    session_id: UUID
    turn_id: UUID
    utterance_id: UUID


@dataclass(frozen=True, slots=True)
class VoiceChunk:
    session: VoiceSession
    sequence: int
    pcm: bytes
    sample_rate: int
    channels: int
    final: bool = False
    format: Literal["pcm_s16le"] = "pcm_s16le"

    def __post_init__(self) -> None:
        if type(self.sequence) is not int or self.sequence < 0:
            raise ValueError("VOICE_CHUNK_SEQUENCE_INVALID")
        if type(self.sample_rate) is not int or not 8000 <= self.sample_rate <= 192000:
            raise ValueError("VOICE_CHUNK_SAMPLE_RATE_INVALID")
        if (
            type(self.channels) is not int
            or self.channels not in {1, 2}
            or self.format != "pcm_s16le"
        ):
            raise ValueError("VOICE_CHUNK_FORMAT_INVALID")
        if (
            type(self.final) is not bool
            or not isinstance(self.pcm, bytes)
            or len(self.pcm) % (2 * self.channels)
        ):
            raise ValueError("VOICE_CHUNK_PCM_INVALID")


@runtime_checkable
class StreamingVoiceBackend(VoiceBackend, Protocol):
    """Optional incremental PCM capability beside the complete-WAV renderer.

    Closing the iterator must stop generation and release renderer resources.
    Emitted PCM must already carry provider/backend voice decisions; clients only
    play samples. Fish cloud currently implements this capability for Safari,
    while WAV-only local/remote renderers may continue implementing VoiceBackend.
    """

    def stream(
        self, item: SpeechUtterance, session: VoiceSession
    ) -> Iterator[VoiceChunk]: ...


def validated_chunks(
    session: VoiceSession, chunks: Iterator[VoiceChunk]
) -> Iterator[VoiceChunk]:
    """Reject discontinuity, identity/format changes and unclosed streams."""
    expected = 0
    audio_format = None
    finished = False
    try:
        for chunk in chunks:
            if finished or chunk.session != session or chunk.sequence != expected:
                raise ValueError("VOICE_STREAM_DISCONTINUITY")
            current_format = (chunk.sample_rate, chunk.channels, chunk.format)
            if audio_format is not None and current_format != audio_format:
                raise ValueError("VOICE_STREAM_FORMAT_CHANGED")
            audio_format = current_format
            expected += 1
            finished = chunk.final
            yield chunk
        if not finished:
            raise ValueError("VOICE_STREAM_INCOMPLETE")
    finally:
        close = getattr(chunks, "close", None)
        if close is not None:
            close()
