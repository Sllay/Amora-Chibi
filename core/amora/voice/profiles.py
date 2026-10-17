from __future__ import annotations

import hashlib
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


class VoiceBackendDescriptor(BaseModel):
    id: str = Field(min_length=1, max_length=96)
    model_id: str = Field(min_length=1, max_length=160)
    language: str = Field(default="pt", min_length=2, max_length=16)
    sample_rate_hz: int = Field(default=24_000, ge=8_000, le=96_000)


class VoiceLabSelection(BaseModel):
    default_speaker_id: int = Field(default=0, ge=0, le=255)
    default_profile_id: str = Field(default="NATURAL", min_length=1, max_length=64)
    canonical: bool = False


class VoiceDspProfile(BaseModel):
    pitch_semitones: float = Field(default=0.0, ge=-6.0, le=6.0)
    drive: float = Field(default=1.0, ge=0.75, le=2.5)
    bit_depth: int = Field(default=16, ge=8, le=16)
    sample_hold: int = Field(default=1, ge=1, le=6)
    digital_mix: float = Field(default=0.0, ge=0.0, le=0.8)
    ring_mod_hz: float = Field(default=0.0, ge=0.0, le=120.0)
    ring_mix: float = Field(default=0.0, ge=0.0, le=0.25)


class VoiceProfile(BaseModel):
    id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=96)
    description: str = Field(default="", max_length=320)
    speed: float = Field(default=1.0, ge=0.7, le=1.3)
    silence_scale: float = Field(default=1.0, ge=0.5, le=1.5)
    num_steps: int = Field(default=8, ge=2, le=16)
    output_gain: float = Field(default=0.92, ge=0.5, le=1.0)
    dsp: VoiceDspProfile = Field(default_factory=VoiceDspProfile)


class EmotionVoiceModulation(BaseModel):
    enabled: bool = True
    speed_arousal: float = Field(default=0.06, ge=-0.25, le=0.25)
    speed_valence: float = Field(default=0.015, ge=-0.25, le=0.25)
    silence_arousal: float = Field(default=-0.08, ge=-0.4, le=0.4)
    silence_valence: float = Field(default=-0.02, ge=-0.4, le=0.4)
    gain_arousal: float = Field(default=0.04, ge=-0.25, le=0.25)
    gain_valence: float = Field(default=0.01, ge=-0.25, le=0.25)
    speed_min: float = Field(default=0.85, ge=0.7, le=1.3)
    speed_max: float = Field(default=1.15, ge=0.7, le=1.3)
    silence_min: float = Field(default=0.75, ge=0.5, le=1.5)
    silence_max: float = Field(default=1.2, ge=0.5, le=1.5)
    gain_min: float = Field(default=0.75, ge=0.5, le=1.0)
    gain_max: float = Field(default=1.0, ge=0.5, le=1.0)

    @model_validator(mode="after")
    def validate_bounds(self) -> "EmotionVoiceModulation":
        if self.speed_min > self.speed_max:
            raise ValueError("speed_min must be <= speed_max")
        if self.silence_min > self.silence_max:
            raise ValueError("silence_min must be <= silence_max")
        if self.gain_min > self.gain_max:
            raise ValueError("gain_min must be <= gain_max")
        return self


class VoiceProfileCatalog(BaseModel):
    schema_version: int = Field(default=1, ge=1, le=16)
    revision: str = Field(min_length=1, max_length=128)
    backend: VoiceBackendDescriptor
    lab: VoiceLabSelection = Field(default_factory=VoiceLabSelection)
    profiles: list[VoiceProfile] = Field(min_length=1, max_length=32)
    emotion_modulation: EmotionVoiceModulation = Field(default_factory=EmotionVoiceModulation)

    @model_validator(mode="after")
    def validate_catalog(self) -> "VoiceProfileCatalog":
        ids = [profile.id for profile in self.profiles]
        if len(ids) != len(set(ids)):
            raise ValueError("voice profile ids must be unique")
        if self.lab.default_profile_id not in ids:
            raise ValueError("lab.default_profile_id must reference a profile")
        return self


class VoiceProfileCatalogStore:
    """Loads the editable server-side catalog and fingerprints the exact bytes.

    The file lives outside the Python package so BK can change bounded profile/DSP
    parameters, `git pull`, restart `amora serve`, and let Android refresh the
    catalog without rebuilding the APK.
    """

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(__file__).resolve().parents[3] / "config" / "voice_profiles.json"

    def load(self) -> tuple[VoiceProfileCatalog, str]:
        raw = self.path.read_bytes()
        catalog = VoiceProfileCatalog.model_validate_json(raw)
        digest = hashlib.sha256(raw).hexdigest()
        return catalog, f'"{digest}"'
