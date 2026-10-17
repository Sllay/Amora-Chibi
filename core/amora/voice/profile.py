from __future__ import annotations

import hashlib
import json
from pathlib import Path

from pydantic import BaseModel, Field, model_validator


PROFILE_PATH = Path(__file__).with_name("amora_voice_profile.json")


class VoiceBaseProfile(BaseModel):
    backend: str = Field(pattern=r"^[a-z0-9][a-z0-9._-]{1,63}$")
    speaker_id: int = Field(ge=0, le=64)
    language: str = Field(pattern=r"^[a-z]{2,8}$")


class VoiceSynthesisProfile(BaseModel):
    speed: float = Field(ge=0.75, le=1.25)
    silence_scale: float = Field(ge=0.5, le=1.5)
    num_steps: int = Field(ge=2, le=16)


class VoiceSignatureProfile(BaseModel):
    low_shelf_db: float = Field(ge=-4.0, le=4.0)
    presence_db: float = Field(ge=-4.0, le=4.0)
    air_db: float = Field(ge=-4.0, le=4.0)
    soft_saturation: float = Field(ge=0.0, le=0.3)
    fantasy_texture: float = Field(ge=0.0, le=0.15)
    compressor_threshold: float = Field(ge=0.35, le=0.95)
    compressor_ratio: float = Field(ge=1.0, le=4.0)
    compressor_attack_ms: float = Field(ge=1.0, le=40.0)
    compressor_release_ms: float = Field(ge=20.0, le=300.0)
    output_gain: float = Field(ge=0.5, le=1.2)
    limiter: float = Field(ge=0.75, le=1.0)


class VoiceProsodyProfile(BaseModel):
    min_speed: float = Field(ge=0.7, le=1.0)
    max_speed: float = Field(ge=1.0, le=1.35)
    arousal_rate_amount: float = Field(ge=0.0, le=0.15)
    pitch_to_presence_db: float = Field(ge=0.0, le=2.5)
    valence_to_air_db: float = Field(ge=0.0, le=1.5)
    arousal_to_presence_db: float = Field(ge=0.0, le=1.5)

    @model_validator(mode="after")
    def validate_window(self) -> "VoiceProsodyProfile":
        if self.min_speed > self.max_speed:
            raise ValueError("min_speed must be <= max_speed")
        return self


class VoiceExpressionProfile(BaseModel):
    speed_scale: float = Field(ge=0.85, le=1.15)
    presence_delta_db: float = Field(ge=-2.0, le=2.0)
    air_delta_db: float = Field(ge=-2.0, le=2.0)
    texture_scale: float = Field(ge=0.0, le=3.0)
    saturation_scale: float = Field(ge=0.0, le=2.0)


class AmoraVoiceProfile(BaseModel):
    schema_version: int = Field(ge=1, le=1)
    profile_id: str = Field(pattern=r"^amora-voice-v[0-9]+$")
    revision: int = Field(ge=1)
    base: VoiceBaseProfile
    synthesis: VoiceSynthesisProfile
    signature: VoiceSignatureProfile
    prosody: VoiceProsodyProfile
    profiles: dict[str, VoiceExpressionProfile]

    @model_validator(mode="after")
    def validate_identity_and_profiles(self) -> "AmoraVoiceProfile":
        # Speaker 0 was physically selected by BK on Galaxy A15. Tuning data may
        # change without an APK rebuild, but a server edit must not silently swap
        # the person Amora sounds like.
        if self.base.backend != "supertonic3-int8" or self.base.speaker_id != 0 or self.base.language != "pt":
            raise ValueError("amora-voice-v1 base identity must remain Supertonic speaker 0 / pt")
        required = {"NATURAL", "SOFT", "SHY", "SYNTH", "CHIBI"}
        if not required.issubset(self.profiles):
            raise ValueError(f"missing voice profiles: {sorted(required - set(self.profiles))}")
        return self


def load_voice_profile(path: Path = PROFILE_PATH) -> tuple[AmoraVoiceProfile, str, bytes]:
    raw = path.read_bytes()
    payload = json.loads(raw)
    profile = AmoraVoiceProfile.model_validate(payload)
    canonical = json.dumps(
        profile.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")
    etag = hashlib.sha256(canonical).hexdigest()
    return profile, etag, canonical
