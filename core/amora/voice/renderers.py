"""Worker-side synthesis authority. HTTP knows only this protocol."""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

from .contract import RenderInput
from .identity import WorkerVoiceIdentity
from .local_amora import LocalVoiceAudio, MODEL_ID, VOICE_ID

ZERO_COST_REQUIRED = True


@runtime_checkable
class WorkerVoiceRenderer(Protocol):
    identity: WorkerVoiceIdentity

    def state(self) -> dict[str, object]: ...
    def ready(self) -> bool: ...
    def render(self, item: RenderInput) -> LocalVoiceAudio: ...


class SupertonicRendererAdapter:
    """Frozen engine/DSP unchanged. Cues remain explicitly unsupported."""

    identity = WorkerVoiceIdentity("supertonic", MODEL_ID, VOICE_ID)

    def __init__(self, renderer):
        self.renderer = renderer

    def state(self):
        return {
            "renderer": self.identity.renderer,
            "model": self.identity.model,
            "voice": self.identity.voice,
            "streaming_capable": False,
            "protocols": ["amora.voice.render.v1", "amora.voice.render.v2"],
            "supported_cues": [],
            "zero_cost_required": ZERO_COST_REQUIRED,
        }

    def ready(self):
        self.renderer._ensure_engine()
        self.renderer._world._ensure_loaded()
        return True

    def render(self, item):
        return self.renderer.render(item)


def renderer_from_environment() -> WorkerVoiceRenderer:
    name = os.getenv("AMORA_VOICE_RENDERER", "supertonic")
    if os.getenv("AMORA_ZERO_COST_REQUIRED", "true").lower() != "true":
        raise ValueError("ZERO_COST_REQUIRED")
    if name == "omnivoice":
        # This import belongs exclusively to worker startup, never Core routing.
        # No Lightning SDK, billing API, GPU selection or fallback exists.
        from .omnivoice_renderer import OmniVoiceRenderer, OmniVoiceSettings

        if os.getenv("AMORA_VOICE_ZERO_COST_CONFIRMED", "").lower() != "true":
            raise ValueError("VOICE_FREE_CPU_HOST_CONFIRMATION_REQUIRED")
        return OmniVoiceRenderer(OmniVoiceSettings.from_environment())
    if name == "supertonic":
        from amora.settings import RuntimePaths
        from .local_amora import AmoraLocalVoiceRenderer

        return SupertonicRendererAdapter(
            AmoraLocalVoiceRenderer(RuntimePaths.from_environment())
        )
    raise ValueError("VOICE_RENDERER_UNSUPPORTED")
