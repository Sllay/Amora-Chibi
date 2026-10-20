from __future__ import annotations

from datetime import UTC, datetime

from amora.conversation.composer import LocalSpeechComposer
from amora.emotion import MoodState
from amora.voice.expression import VoiceProsodyPlanner


def _mood(valence: float, arousal: float, dominance: float) -> MoodState:
    return MoodState(
        valence=valence,
        arousal=arousal,
        dominance=dominance,
        updated_at=datetime.now(UTC),
    )


def test_speech_style_carries_exact_core_expression_plan() -> None:
    mood = _mood(0.42, 0.31, -0.08)
    expected = VoiceProsodyPlanner().safe_plan(mood)
    style = LocalSpeechComposer().style_for_mood(mood)

    assert style.expression is not None
    assert style.expression.model_dump(mode="json") == expected.model_dump(mode="json")


def test_neutral_expression_transport_is_identity_safe() -> None:
    style = LocalSpeechComposer().style_for_mood(_mood(0.0, 0.0, 0.0))

    assert style.expression is not None
    assert style.expression.id == "AMORA_VOICE_EXPRESSION_V2"
    assert style.expression.rate_scale == 1.0
    assert style.expression.pause_scale == 1.0
    assert style.expression.energy_scale == 1.0
    assert style.expression.pitch_range_scale == 1.0
    assert style.expression.f0_register_scale == 1.0
    assert style.expression.fallback is False
