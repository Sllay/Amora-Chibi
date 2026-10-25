"""Rendering capability. Delivery/ACK and canonical state remain in the Core."""
from __future__ import annotations

from typing import Protocol
from amora.conversation import SpeechUtterance
from .local_amora import LocalVoiceAudio


class VoiceBackend(Protocol):
    def state(self) -> dict[str, object]: ...
    def render(self, item: SpeechUtterance) -> LocalVoiceAudio: ...
