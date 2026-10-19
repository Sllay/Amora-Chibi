from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from amora.emotion import EmotionType, EntityAffect, MoodState


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


class VoiceExpressionPlan(BaseModel):
    """Provider-independent, bounded vocal expression around Amora's fixed identity.

    The plan deliberately does not contain an absolute pitch shift or formant/VTL
    transform. Identity belongs to VoiceSignature; this layer may only modulate
    delivery around that identity.
    """

    id: str = "AMORA_VOICE_EXPRESSION_V1"
    rate_scale: float = Field(default=1.0, ge=0.90, le=1.08)
    pause_scale: float = Field(default=1.0, ge=0.88, le=1.16)
    energy_scale: float = Field(default=1.0, ge=0.88, le=1.06)
    pitch_range_scale: float = Field(default=1.0, ge=0.90, le=1.08)
    softness: float = Field(default=0.25, ge=0.0, le=1.0)
    firmness: float = Field(default=0.15, ge=0.0, le=1.0)
    source_emotion: EmotionType | None = None
    source_intensity: float = Field(default=0.0, ge=0.0, le=1.0)
    fallback: bool = False
    reason_codes: list[str] = Field(default_factory=list)

    @classmethod
    def neutral(cls, *, fallback: bool = False, reason: str | None = None) -> "VoiceExpressionPlan":
        reasons = ["VOICE_EXPRESSION_NEUTRAL"]
        if reason:
            reasons.append(reason)
        return cls(fallback=fallback, reason_codes=reasons)


@dataclass(frozen=True, slots=True)
class _EmotionBias:
    rate: float = 0.0
    pause: float = 0.0
    energy: float = 0.0
    pitch_range: float = 0.0
    softness: float = 0.0
    firmness: float = 0.0


class VoiceProsodyPlanner:
    """Maps Core affect to a conservative expression plan.

    Design rules:
    - emotion never changes Amora's canonical timbre/age/formants;
    - no absolute pitch-shift knob exists here;
    - changes are intentionally small and clamped;
    - `safe_plan()` must always be able to fall back to a neutral plan.
    """

    _BIAS: dict[EmotionType, _EmotionBias] = {
        EmotionType.JOY: _EmotionBias(
            rate=0.016,
            pause=-0.020,
            energy=0.014,
            pitch_range=0.020,
            softness=0.035,
        ),
        EmotionType.GRATITUDE: _EmotionBias(
            rate=-0.006,
            pause=0.025,
            energy=-0.006,
            pitch_range=-0.006,
            softness=0.120,
            firmness=-0.025,
        ),
        EmotionType.RELIEF: _EmotionBias(
            rate=-0.020,
            pause=0.060,
            energy=-0.020,
            pitch_range=-0.020,
            softness=0.080,
            firmness=-0.030,
        ),
        EmotionType.ANGER: _EmotionBias(
            rate=0.020,
            pause=-0.040,
            energy=0.025,
            pitch_range=0.030,
            softness=-0.100,
            firmness=0.250,
        ),
        EmotionType.IRRITATION: _EmotionBias(
            rate=0.010,
            pause=-0.022,
            energy=0.014,
            pitch_range=0.014,
            softness=-0.055,
            firmness=0.150,
        ),
        EmotionType.FEAR: _EmotionBias(
            rate=0.022,
            pause=-0.010,
            energy=-0.005,
            pitch_range=0.038,
            softness=-0.030,
            firmness=-0.080,
        ),
        EmotionType.DISAPPOINTMENT: _EmotionBias(
            rate=-0.035,
            pause=0.080,
            energy=-0.035,
            pitch_range=-0.040,
            softness=0.030,
            firmness=-0.060,
        ),
    }

    def plan(
        self,
        mood: MoodState,
        *,
        emotion: EmotionType | None = None,
        intensity: float = 0.0,
        affect: EntityAffect | None = None,
    ) -> VoiceExpressionPlan:
        valence = _clamp(mood.valence, -1.0, 1.0)
        arousal = _clamp(mood.arousal, 0.0, 1.0)
        dominance = _clamp(mood.dominance, -1.0, 1.0)
        strength = _clamp(intensity, 0.0, 1.0)

        # Continuous mood provides the small baseline motion. Crucially, the F0
        # median stays untouched; only contour range may breathe a little.
        rate = 1.0 + 0.025 * arousal + 0.006 * valence
        pause = 1.0 - 0.040 * arousal - 0.008 * valence
        energy = 1.0 + 0.024 * arousal + 0.010 * dominance
        pitch_range = 1.0 + 0.030 * arousal
        softness = 0.25 + 0.08 * max(valence, 0.0) - 0.04 * max(dominance, 0.0)
        firmness = 0.15 + 0.16 * max(dominance, 0.0) + 0.04 * arousal

        reasons = ["VOICE_EXPRESSION_FROM_MOOD"]

        if affect is not None:
            affection = _clamp(affect.affection, 0.0, 1.0)
            gratitude = _clamp(affect.gratitude, 0.0, 1.0)
            anger = _clamp(affect.anger, 0.0, 1.0)
            irritation = _clamp(affect.irritation, 0.0, 1.0)
            fear = _clamp(affect.fear, 0.0, 1.0)
            softness += 0.14 * affection + 0.08 * gratitude - 0.08 * anger - 0.05 * irritation
            firmness += 0.16 * anger + 0.10 * irritation - 0.06 * fear
            reasons.append("VOICE_EXPRESSION_FROM_DIRECTED_AFFECT")

        if emotion is not None and strength > 0.0:
            bias = self._BIAS.get(emotion)
            if bias is not None:
                rate += bias.rate * strength
                pause += bias.pause * strength
                energy += bias.energy * strength
                pitch_range += bias.pitch_range * strength
                softness += bias.softness * strength
                firmness += bias.firmness * strength
                reasons.append(f"VOICE_EXPRESSION_EMOTION_{emotion.value.upper()}")

        return VoiceExpressionPlan(
            rate_scale=_clamp(rate, 0.90, 1.08),
            pause_scale=_clamp(pause, 0.88, 1.16),
            energy_scale=_clamp(energy, 0.88, 1.06),
            pitch_range_scale=_clamp(pitch_range, 0.90, 1.08),
            softness=_clamp(softness, 0.0, 1.0),
            firmness=_clamp(firmness, 0.0, 1.0),
            source_emotion=emotion,
            source_intensity=strength,
            reason_codes=reasons,
        )

    def safe_plan(
        self,
        mood: MoodState,
        *,
        emotion: EmotionType | None = None,
        intensity: float = 0.0,
        affect: EntityAffect | None = None,
    ) -> VoiceExpressionPlan:
        try:
            return self.plan(mood, emotion=emotion, intensity=intensity, affect=affect)
        except Exception:
            # Voice expression is optional. A failure here must never make Amora
            # mute or corrupt her identity path.
            return VoiceExpressionPlan.neutral(
                fallback=True,
                reason="VOICE_EXPRESSION_FAILSAFE",
            )
