"""Experimental official OmniVoice renderer; imported only by the voice worker.

Heavy dependencies are lazy, never installed/imported by the Core image.
CPU float32 is unconditional. Generation is complete-WAV, not streaming.
"""

from __future__ import annotations

from dataclasses import dataclass
from importlib.metadata import version
import math
import os
from pathlib import Path
from threading import RLock
from time import perf_counter

from .audio_contract import encode_pcm16, validate_wav
from .contract import RenderInput
from .identity import WorkerVoiceIdentity
from .local_amora import LocalVoiceAudio, LocalVoiceUnavailable
from .reference import OmniVoiceReferenceManifest

MODEL_ID = "k2-fsa/OmniVoice"
# Verified against the official 0.2.1 wheel's _NONVERBAL_PATTERN and README.
OMNIVOICE_TAGS = {
    "LAUGHTER": "[laughter]",
    "SIGH": "[sigh]",
    "SURPRISE": "[surprise-oh]",
    "DISSATISFACTION": "[dissatisfaction-hnn]",
}


def map_cues(text: str, cues: tuple[str, ...]) -> tuple[str, tuple[str, ...]]:
    unique = tuple(dict.fromkeys(cues))
    tags = [OMNIVOICE_TAGS[cue] for cue in unique if cue in OMNIVOICE_TAGS]
    return " ".join([*tags, text]).strip(), tuple(
        cue for cue in unique if cue not in OMNIVOICE_TAGS
    )


@dataclass(frozen=True, slots=True)
class OmniVoiceSettings:
    manifest: Path
    prompt: Path
    model: str = MODEL_ID
    num_step: int = 32
    threads: int = 4
    model_directory: Path | None = None

    def __post_init__(self):
        if (
            self.model != MODEL_ID
            or self.num_step not in {16, 24, 32}
            or not 1 <= self.threads <= 4
        ):
            raise ValueError("OMNIVOICE_CONFIGURATION_INVALID")

    @classmethod
    def from_environment(cls):
        manifest = os.environ.get("AMORA_OMNIVOICE_REFERENCE_MANIFEST", "")
        prompt = os.environ.get("AMORA_OMNIVOICE_PROMPT", "")
        if not manifest or not prompt:
            raise ValueError("OMNIVOICE_REFERENCE_REQUIRED")
        return cls(
            Path(manifest).expanduser(),
            Path(prompt).expanduser(),
            model=os.getenv("AMORA_OMNIVOICE_MODEL", MODEL_ID),
            num_step=int(os.getenv("AMORA_OMNIVOICE_NUM_STEP", "32")),
            threads=int(os.getenv("AMORA_OMNIVOICE_THREADS", "4")),
            model_directory=Path(
                os.environ["AMORA_OMNIVOICE_MODEL_DIRECTORY"]
            ).expanduser()
            if os.getenv("AMORA_OMNIVOICE_MODEL_DIRECTORY")
            else None,
        )


def load_official_model(settings: OmniVoiceSettings):
    if version("omnivoice") != "0.2.1":
        raise ValueError("OMNIVOICE_VERSION_MISMATCH")
    import torch
    from omnivoice import OmniVoice

    torch.set_num_threads(settings.threads)
    source = settings.model
    if settings.model_directory is not None:
        # A pinned snapshot produced by the explicit download command. The
        # official loader otherwise resolves repo IDs at main, ignoring revision.
        source = str(settings.model_directory.resolve(strict=True))
    model = OmniVoice.from_pretrained(
        source,
        device_map="cpu",
        dtype=torch.float32,
        load_asr=False,
    )
    # The official loader loads the audio tokenizer separately without a dtype.
    # Pin that component too: no mixed precision, GPU or weight quantization.
    model.to(device="cpu", dtype=torch.float32)
    model.audio_tokenizer.to(device="cpu", dtype=torch.float32)
    model.eval()
    model.audio_tokenizer.eval()
    if model.sampling_rate != 24000:
        raise ValueError("OMNIVOICE_SAMPLE_RATE_INVALID")
    return model


def load_official_prompt(path: Path):
    from omnivoice import VoiceClonePrompt

    # Official loader uses torch.load(weights_only=True); never arbitrary pickle.
    return VoiceClonePrompt.load(str(path), map_location="cpu")


class OmniVoiceRenderer:
    def __init__(
        self, settings: OmniVoiceSettings, *, model_loader=None, prompt_loader=None
    ):
        self.settings = settings
        self._model_loader = model_loader or load_official_model
        self._prompt_loader = prompt_loader or load_official_prompt
        self._lock = RLock()
        self._render_lock = RLock()
        self._attempted = False
        self._model = self._prompt = self._manifest = None
        self._identity = None
        self._error = None
        self.model_load_seconds = self.prompt_load_seconds = None

    @property
    def identity(self) -> WorkerVoiceIdentity:
        if self._identity is None:
            raise LocalVoiceUnavailable("VOICE_IDENTITY_UNAVAILABLE")
        return self._identity

    def ready(self) -> bool:
        with self._lock:
            try:
                if not self._attempted:
                    self._attempted = True
                    manifest = OmniVoiceReferenceManifest.load(self.settings.manifest)
                    manifest.validate_files(
                        self.settings.manifest, self.settings.prompt
                    )
                    start = perf_counter()
                    self._model = self._model_loader(self.settings)
                    self.model_load_seconds = perf_counter() - start
                    start = perf_counter()
                    self._prompt = self._prompt_loader(self.settings.prompt)
                    self.prompt_load_seconds = perf_counter() - start
                    tokens = self._prompt.ref_audio_tokens
                    if (
                        self._model.sampling_rate != 24000
                        or self._prompt.ref_text != manifest.reference_text
                        or not math.isfinite(self._prompt.ref_rms)
                        or not 0 < self._prompt.ref_rms <= 1
                        or tokens.ndim != 2
                        or tokens.shape[0] != self._model.config.num_audio_codebook
                        or not 0 < tokens.shape[1] <= 2000
                    ):
                        raise ValueError("OMNIVOICE_RUNTIME_INVALID")
                    self._manifest = manifest
                    self._identity = WorkerVoiceIdentity(
                        "omnivoice", MODEL_ID, manifest.voice_id
                    )
                if (
                    self._manifest is None
                    or self._model is None
                    or self._prompt is None
                ):
                    return False
                # Detect replacement/corruption without generating or reloading.
                if (
                    OmniVoiceReferenceManifest.load(self.settings.manifest)
                    != self._manifest
                ):
                    raise ValueError("VOICE_MANIFEST_CHANGED")
                self._manifest.validate_files(
                    self.settings.manifest, self.settings.prompt
                )
                self._error = None
                return True
            except Exception:
                # Details may contain reference transcript, paths or library data.
                self._error = "OMNIVOICE_RUNTIME_UNAVAILABLE"
                return False

    def state(self):
        return {
            "renderer": "omnivoice",
            "model": MODEL_ID,
            "voice": self._identity.voice if self._identity else None,
            "experimental": True,
            "streaming_capable": False,
            "device": "cpu",
            "dtype": "float32",
            "protocols": ["amora.voice.render.v2"],
            "num_step": self.settings.num_step,
            "guidance_scale": 2.0,
            "class_temperature": 0.0,
            "supported_cues": list(OMNIVOICE_TAGS),
            "unsupported_controls": ["volume", "expression"],
            "available": self._identity is not None and self._error is None,
            "last_error": self._error,
            "zero_cost_required": True,
            "model_load_seconds": self.model_load_seconds,
            "prompt_load_seconds": self.prompt_load_seconds,
        }

    def render(self, item: RenderInput) -> LocalVoiceAudio:
        with self._render_lock:
            if not self.ready():
                raise LocalVoiceUnavailable("OMNIVOICE_RUNTIME_UNAVAILABLE")
            text, unsupported = map_cues(item.text, item.cues)
            if unsupported:
                raise LocalVoiceUnavailable("VOICE_CUE_UNSUPPORTED")
            start = perf_counter()
            # No ref_audio/ref_text per request: the reusable prompt is loaded once.
            audios = self._model.generate(
                text=text,
                language="pt",
                voice_clone_prompt=self._prompt,
                speed=item.style.rate,
                num_step=self.settings.num_step,
                guidance_scale=2.0,
                class_temperature=0.0,
            )
            seconds = perf_counter() - start
            if (
                not isinstance(audios, list)
                or len(audios) != 1
                or getattr(audios[0], "ndim", 1) != 1
            ):
                raise LocalVoiceUnavailable("VOICE_INVALID_SAMPLES")
            body = encode_pcm16(audios[0], self._model.sampling_rate)
            rate, frames = validate_wav(body)
            return LocalVoiceAudio(body, rate, frames, seconds, 0.0)
