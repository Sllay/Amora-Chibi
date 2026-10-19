from __future__ import annotations

from datetime import UTC, datetime

from amora.emotion import EmotionType, EntityAffect, MoodState
from amora.voice.expression import VoiceExpressionPlan, VoiceProsodyPlanner


def _mood(valence: float = 0.0, arousal: float = 0.0, dominance: float = 0.0) -> MoodState:
    return MoodState(
        valence=valence,
        arousal=arousal,
        dominance=dominance,
        updated_at=datetime.now(UTC),
    )


def test_neutral_plan_preserves_identity_region() -> None:
    plan = VoiceProsodyPlanner().plan(_mood())
    assert plan.rate_scale == 1.0
    assert plan.pause_scale == 1.0
    assert plan.energy_scale == 1.0
    assert plan.pitch_range_scale == 1.0
    assert plan.fallback is False
    assert not hasattr(plan, "pitch_shift")
    assert not hasattr(plan, "formant_shift")


def test_anger_is_firmer_without_large_identity_changes() -> None:
    planner = VoiceProsodyPlanner()
    neutral = planner.plan(_mood())
    angry = planner.plan(
        _mood(valence=-0.55, arousal=0.78, dominance=0.55),
        emotion=EmotionType.ANGER,
        intensity=0.8,
    )
    assert angry.rate_scale > neutral.rate_scale
    assert angry.energy_scale > neutral.energy_scale
    assert angry.pitch_range_scale > neutral.pitch_range_scale
    assert angry.firmness > neutral.firmness
    assert angry.softness < neutral.softness
    assert 0.90 <= angry.rate_scale <= 1.08
    assert 0.88 <= angry.energy_scale <= 1.06
    assert 0.90 <= angry.pitch_range_scale <= 1.08


def test_disappointment_is_slower_quieter_and_more_paused() -> None:
    plan = VoiceProsodyPlanner().plan(
        _mood(valence=-0.65, arousal=0.12, dominance=-0.30),
        emotion=EmotionType.DISAPPOINTMENT,
        intensity=0.85,
    )
    assert plan.rate_scale < 1.0
    assert plan.energy_scale < 1.0
    assert plan.pause_scale > 1.0
    assert plan.pitch_range_scale < 1.0


def test_directed_affection_softens_delivery_without_touching_identity() -> None:
    affect = EntityAffect(
        entity_id="PERSON_BK",
        affection=0.9,
        gratitude=0.55,
        updated_at=datetime.now(UTC),
    )
    planner = VoiceProsodyPlanner()
    plain = planner.plan(_mood(valence=0.55, arousal=0.18))
    tender = planner.plan(_mood(valence=0.55, arousal=0.18), affect=affect)
    assert tender.softness > plain.softness
    assert tender.rate_scale == plain.rate_scale
    assert tender.pitch_range_scale == plain.pitch_range_scale


def test_safe_plan_returns_neutral_if_optional_module_fails(monkeypatch) -> None:
    planner = VoiceProsodyPlanner()

    def boom(*args, **kwargs):
        raise RuntimeError("simulated prosody failure")

    monkeypatch.setattr(planner, "plan", boom)
    plan = planner.safe_plan(_mood(valence=0.9, arousal=0.9, dominance=0.9))
    assert plan == VoiceExpressionPlan.neutral(
        fallback=True,
        reason="VOICE_EXPRESSION_FAILSAFE",
    )
    assert plan.fallback is True
