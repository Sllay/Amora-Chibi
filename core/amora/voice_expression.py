from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, Field

from amora.emotion import EmotionType, EntityAffect, MoodState


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, float(value)))


class VoiceExpressionPlan(BaseModel):
    """Provider-independent, bounded vocal expression around Amora's fixed identity.

    Identity still belongs to VoiceSignature. This layer may change delivery and
    may move the F0 register only inside a tiny bounded region; it never changes
    formants/VTL, the frozen child morphology, or the fixed VoiceSignature texture.
    """

    id: str = "AMORA_VOICE_EXPRESSION_V2"
    rate_scale: float = Field(default=1.0, ge=0.90, le=1.08)
    pause_scale: float = Field(default=1.0, ge=0.88, le=1.16)
    energy_scale: float = Field(default=1.0, ge=0.88, le=1.06)
    pitch_range_scale: float = Field(default=1.0, ge=0.90, le=1.08)
    f0_register_scale: float = Field(default=1.0, ge=0.97, le=1.02)
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
    f0_register: float = 0.0
    softness: float = 0.0
    firmness: float = 0.0


class VoiceProsodyPlanner:
    """Maps Core affect to a perceptible but still bounded expression plan.

    V2 keeps the identity invariants from V1 but responds to BK's physical
    audition: the first pass was too subtle in normal conversation. Sadness and
    tenderness therefore get clearer cadence/energy cues, anger gets firmer
    delivery, and a tiny F0-register control is allowed for emotional colour.

    Design rules:
    - emotion never changes canonical timbre/age/formants/VTL;
    - f0_register_scale is tiny (0.97..1.02), not a new identity/pitch effect;
    - changes stay bounded and continuous with intensity;
    - `safe_plan()` always falls back to neutral expression on failure.
    """

    _BIAS: dict[EmotionType, _EmotionBias] = {
        EmotionType.JOY: _EmotionBias(
            rate=0.030,
            pause=-0.040,
            energy=0.025,
            pitch_range=0.040,
            f0_register=0.006,
            softness=0.055,
        ),
        EmotionType.GRATITUDE: _EmotionBias(
            rate=-0.040,
            pause=0.080,
            energy=-0.030,
            pitch_range=-0.015,
            f0_register=-0.006,
            softness=0.250,
            firmness=-0.050,
        ),
        EmotionType.RELIEF: _EmotionBias(
            rate=-0.050,
            pause=0.100,
            energy=-0.040,
            pitch_range=-0.035,
            f0_register=-0.010,
            softness=0.160,
            firmness=-0.060,
        ),
        EmotionType.ANGER: _EmotionBias(
            rate=0.035,
            pause=-0.070,
            energy=0.050,
            pitch_range=0.050,
            f0_register=0.006,
            softness=-0.180,
            firmness=0.420,
        ),
        EmotionType.IRRITATION: _EmotionBias(
            rate=0.022,
            pause=-0.045,
            energy=0.030,
            pitch_range=0.026,
            f0_register=0.003,
            softness=-0.100,
            firmness=0.260,
        ),
        EmotionType.FEAR: _EmotionBias(
            rate=0.035,
            pause=-0.018,
            energy=-0.012,
            pitch_range=0.060,
            f0_register=0.010,
            softness=-0.040,
            firmness=-0.110,
        ),
        EmotionType.DISAPPOINTMENT: _EmotionBias(
            rate=-0.075,
            pause=0.140,
            energy=-0.080,
            pitch_range=-0.075,
            f0_register=-0.030,
            softness=0.140,
            firmness=-0.120,
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

        rate = 1.0 + 0.025 * arousal + 0.006 * valence
        pause = 1.0 - 0.040 * arousal - 0.008 * valence
        energy = 1.0 + 0.024 * arousal + 0.010 * dominance
        pitch_range = 1.0 + 0.030 * arousal
        f0_register = 1.0
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
                f0_register += bias.f0_register * strength
                softness += bias.softness * strength
                firmness += bias.firmness * strength
                reasons.append(f"VOICE_EXPRESSION_EMOTION_{emotion.value.upper()}")

        return VoiceExpressionPlan(
            rate_scale=_clamp(rate, 0.90, 1.08),
            pause_scale=_clamp(pause, 0.88, 1.16),
            energy_scale=_clamp(energy, 0.88, 1.06),
            pitch_range_scale=_clamp(pitch_range, 0.90, 1.08),
            f0_register_scale=_clamp(f0_register, 0.97, 1.02),
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
            return VoiceExpressionPlan.neutral(
                fallback=True,
                reason="VOICE_EXPRESSION_FAILSAFE",
            )
