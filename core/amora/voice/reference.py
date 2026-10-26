"""Local, versioned laboratory voice assets. No canonical reference is shipped."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .audio_contract import inspect_wav

MAX_PROMPT_BYTES = 64 * 1024 * 1024


def file_sha256(path: Path, *, maximum: int = MAX_PROMPT_BYTES) -> str:
    if not 0 < path.stat().st_size <= maximum:
        raise ValueError("VOICE_REFERENCE_FILE_SIZE_INVALID")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        total = 0
        for chunk in iter(lambda: stream.read(65536), b""):
            total += len(chunk)
            if total > maximum:
                raise ValueError("VOICE_REFERENCE_FILE_SIZE_INVALID")
            digest.update(chunk)
    return digest.hexdigest()


class OmniVoiceReferenceManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    voice_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")
    reference_audio: str = Field(min_length=1, max_length=2048)
    reference_text: str = Field(min_length=1, max_length=2000)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    sample_rate: int = Field(ge=8000, le=96000)
    duration_seconds: float = Field(ge=3, le=10, allow_inf_nan=False)
    prompt_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")

    def validate_reference(self, manifest_path: Path) -> Path:
        if (
            not self.reference_text.strip()
            or "://" in self.reference_audio
            or self.voice_id
            in {"AMORA_VOICE_SIGNATURE_V1_SOFT", "AMORA_CHILD_FINAL_B_PLUS60"}
        ):
            raise ValueError("VOICE_REFERENCE_INVALID")
        audio = Path(self.reference_audio).expanduser()
        if not audio.is_absolute():
            audio = manifest_path.parent / audio
        audio = audio.resolve(strict=True)
        if file_sha256(audio, maximum=2 * 1024 * 1024) != self.sha256:
            raise ValueError("VOICE_REFERENCE_HASH_MISMATCH")
        _, samples = inspect_wav(
            audio.read_bytes(),
            sample_rate=self.sample_rate,
            min_seconds=3,
            max_seconds=10,
        )
        if (
            abs(len(samples) / self.sample_rate - self.duration_seconds)
            > 1 / self.sample_rate
        ):
            raise ValueError("VOICE_REFERENCE_DURATION_MISMATCH")
        rms = math.sqrt(sum((sample / 32768) ** 2 for sample in samples) / len(samples))
        if rms < 0.003 or sum(abs(v) < 16 for v in samples) / len(samples) > 0.99:
            raise ValueError("VOICE_REFERENCE_SILENT")
        return audio

    def validate_files(self, manifest_path: Path, prompt_path: Path) -> Path:
        audio = self.validate_reference(manifest_path)
        if file_sha256(prompt_path) != self.prompt_sha256:
            raise ValueError("VOICE_PROMPT_HASH_MISMATCH")
        return audio

    @classmethod
    def load(cls, path: Path) -> OmniVoiceReferenceManifest:
        if path.stat().st_size > 16384:
            raise ValueError("VOICE_MANIFEST_TOO_LARGE")
        return cls.model_validate(json.loads(path.read_text(encoding="utf-8")))
