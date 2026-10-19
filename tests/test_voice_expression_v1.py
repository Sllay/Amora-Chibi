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
    assert plan.id == "AMORA_VOICE_EXPRESSION_V2"
    assert plan.rate_scale == 1.0
    assert plan.pause_scale == 1.0
    assert plan.energy_scale == 1.0
    assert plan.pitch_range_scale == 1.0
    assert plan.f0_register_scale == 1.0
    assert plan.fallback is False
    assert not hasattr(plan, "pitch_shift")
    assert not hasattr(plan, "formant_shift")


def test_anger_is_clearly_firmer_without_changing_identity() -> None:
    planner = VoiceProsodyPlanner()
    neutral = planner.plan(_mood())
    angry = planner.plan(
        _mood(valence=-0.65, arousal=0.78, dominance=0.55),
        emotion=EmotionType.ANGER,
        intensity=0.90,
        affect=EntityAffect(
            entity_id="PERSON_BK",
            anger=0.75,
            irritation=0.60,
            trust=0.35,
            updated_at=datetime.now(UTC),
        ),
    )
    assert angry.rate_scale > 1.03
    assert angry.energy_scale >= 1.05
    assert angry.pitch_range_scale > 1.05
    assert angry.firmness > 0.70
    assert angry.softness < neutral.softness
    assert 1.0 <= angry.f0_register_scale <= 1.02


def test_disappointment_is_perceptibly_slower_lower_quieter_and_more_paused() -> None:
    plan = VoiceProsodyPlanner().plan(
        _mood(valence=-0.65, arousal=0.10, dominance=-0.30),
        emotion=EmotionType.DISAPPOINTMENT,
        intensity=0.90,
    )
    assert plan.rate_scale < 0.95
    assert plan.energy_scale < 0.95
    assert plan.pause_scale > 1.10
    assert plan.pitch_range_scale < 0.95
    assert plan.f0_register_scale < 0.98


def test_tender_delivery_is_slower_softer_and_still_bounded() -> None:
    affect = EntityAffect(
        entity_id="PERSON_BK",
        affection=0.85,
        gratitude=0.45,
        trust=0.80,
        updated_at=datetime.now(UTC),
    )
    plan = VoiceProsodyPlanner().plan(
        _mood(valence=0.55, arousal=0.16),
        emotion=EmotionType.GRATITUDE,
        intensity=0.70,
        affect=affect,
    )
    assert 0.96 < plan.rate_scale < 0.99
    assert plan.pause_scale > 1.03
    assert plan.energy_scale < 1.0
    assert plan.softness > 0.60
    assert plan.firmness < 0.20
    assert 0.99 <= plan.f0_register_scale <= 1.0


def test_directed_affection_without_discrete_emotion_only_softens_delivery() -> None:
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
    assert tender.f0_register_scale == plain.f0_register_scale


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
