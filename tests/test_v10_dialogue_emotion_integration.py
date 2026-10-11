from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from amora.app import AmoraCore
from amora.cognition.models import CognitiveContext, CognitiveRequest
from amora.cognition.realization import (
    RealizationClosing,
    RealizationError,
    RealizationFrame,
    RealizationMove,
    RealizationValidator,
)
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 11, 19, 30, tzinfo=timezone.utc))


def _context(*, ask_followup: bool = False) -> CognitiveContext:
    return CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime(2026, 10, 11, tzinfo=timezone.utc),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text="O que você acha disso?",
        self_model={"core_identity": {"canonical_name": "Amora"}},
        mood={"valence": 0.0, "arousal": 0.3, "dominance": 0.0},
        drives={},
        dialogue_plan={
            "act": "answer_question",
            "objective": "answer directly",
            "depth": "short",
            "response_moves": ["answer_question"],
            "ask_followup": ask_followup,
            "offer_help": False,
            "closing_mode": "question" if ask_followup else "statement",
        },
        remote_processing_allowed=True,
    )


def test_realization_validator_rejects_move_not_authorized_by_core() -> None:
    frame = RealizationFrame(
        moves=[RealizationMove(kind="offer_help", text="Como posso te ajudar?")],
        closing=RealizationClosing.QUESTION,
    )
    with pytest.raises(RealizationError) as error:
        RealizationValidator.validate(_context(), frame)
    assert error.value.code == "UNAUTHORIZED_REALIZATION_MOVE"


def test_realization_validator_rejects_unsolicited_followup() -> None:
    frame = RealizationFrame(
        moves=[RealizationMove(kind="answer_question", text="Eu não curto muito. E você?")],
        closing=RealizationClosing.QUESTION,
    )
    with pytest.raises(RealizationError) as error:
        RealizationValidator.validate(_context(ask_followup=False), frame)
    assert error.value.code == "FOLLOWUP_NOT_AUTHORIZED"


class StructuredProvider:
    def __init__(self, *, invalid: bool = False) -> None:
        self.invalid = invalid
        self.descriptor = ProviderDescriptor(
            name="structured-fixture-invalid" if invalid else "structured-fixture-valid",
            model="fixture-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=1.0,
            latency_score=1.0,
            cost_score=0.0,
            available=True,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL, PrivacyScope.PRIVATE}),
            supports_streaming=False,
            supports_structured_output=True,
            context_window_tokens=32_000,
            priority=99,
        )

    async def realize(self, context: CognitiveContext) -> dict[str, object]:
        if self.invalid:
            return {
                "moves": [{"kind": "offer_help", "text": "Como posso te ajudar hoje?"}],
                "closing": "question",
            }
        allowed = list(context.dialogue_plan["response_moves"])
        return {
            "moves": [{"kind": allowed[0], "text": "Resposta controlada pelo plano local."}],
            "closing": "statement",
            "_usage": {"input_tokens": 12, "output_tokens": 7, "status_code": 200},
        }


@pytest.mark.asyncio
async def test_invalid_structured_provider_output_never_reaches_speech_queue(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        core.cognition.register_provider(StructuredProvider(invalid=True))
        request = CognitiveRequest(
            text="Amora, o que você acha desse jogo?",
            speaker_id="PERSON_BK",
            target_id="ENTITY_AMORA",
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
        )
        plan = await core.cognition.respond(request, fallback_text="Hm.")
        assert plan.fallback_used is True
        assert "Como posso te ajudar hoje?" not in plan.speech_text
        assert all("Como posso te ajudar hoje?" not in item.speech_text for item in core.cognition.store.list_segments(request.turn_id))
        assert core.cognition.state()["last_realization_rejection"]["code"] == "UNAUTHORIZED_REALIZATION_MOVE"
    finally:
        core.close()


@pytest.mark.asyncio
async def test_valid_structured_provider_is_validated_before_speech(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        provider = StructuredProvider()
        core.cognition.register_provider(provider)
        request = CognitiveRequest(
            text="Amora, o que você acha desse jogo?",
            speaker_id="PERSON_BK",
            target_id="ENTITY_AMORA",
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
        )
        plan = await core.cognition.respond(request)
        assert plan.provider == provider.descriptor.name
        assert plan.fallback_used is False
        assert plan.metadata["realization_validated"] is True
        assert plan.speech_text == "Resposta controlada pelo plano local."
        assert "REALIZATION_MOVES_AUTHORIZED" in plan.reason_codes
    finally:
        core.close()


@pytest.mark.asyncio
async def test_direct_affection_feeds_persistent_emotion_before_dialogue(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        before_mood = core.emotion.current_mood()
        before_affect = core.emotion.affect_for("PERSON_BK")
        before_events = core.emotion.store.event_count()

        _, reply = await core.conversation.observe_completed_utterance(
            "Amora, eu te amo, você é muito especial pra mim.",
            speaker_id="PERSON_BK",
            addressee_id="ENTITY_AMORA",
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=False,
        )

        after_mood = core.emotion.current_mood()
        after_affect = core.emotion.affect_for("PERSON_BK")
        assert reply is not None
        assert core.emotion.store.event_count() == before_events + 1
        assert after_mood.valence > before_mood.valence
        assert after_affect.affection > before_affect.affection
        assert core.conversation.state()["last_emotion_event_id"] is not None
    finally:
        core.close()


@pytest.mark.asyncio
async def test_neutral_direct_question_does_not_manufacture_emotion_event(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        before = core.emotion.store.event_count()
        await core.conversation.observe_completed_utterance(
            "Amora, qual é o assunto de hoje?",
            speaker_id="PERSON_BK",
            addressee_id="ENTITY_AMORA",
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=False,
        )
        assert core.emotion.store.event_count() == before
        assert core.conversation.state()["last_emotion_event_id"] is None
    finally:
        core.close()


@pytest.mark.asyncio
async def test_test_mode_previews_emotion_without_mutating_mood_or_affect(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        before_mood = core.emotion.current_mood().model_dump()
        before_affect = core.emotion.affect_for("PERSON_BK").model_dump()
        before_events = core.emotion.store.event_count()

        await core.conversation.observe_completed_utterance(
            "Amora, eu te odeio, sua inútil.",
            speaker_id="PERSON_BK",
            addressee_id="ENTITY_AMORA",
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=False,
            metadata={
                "test_mode": True,
                "memory_consolidation": False,
                "relationship_learning": False,
                "emotion_persistence": False,
            },
        )

        after_mood = core.emotion.current_mood().model_dump()
        after_affect = core.emotion.affect_for("PERSON_BK").model_dump()
        assert core.emotion.store.event_count() == before_events
        assert after_mood["valence"] == before_mood["valence"]
        assert after_mood["arousal"] == before_mood["arousal"]
        assert after_mood["dominance"] == before_mood["dominance"]
        for key in ("anger", "affection", "trust", "irritation", "fear", "gratitude"):
            assert after_affect[key] == before_affect[key]
        state = core.conversation.state()
        assert state["last_turn_test_mode"] is True
        assert state["last_interaction_appraisal"]["persistent"] is False
    finally:
        core.close()
