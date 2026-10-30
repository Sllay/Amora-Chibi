from __future__ import annotations

from datetime import UTC, datetime
from uuid import uuid4

from amora.cognition.models import CognitiveContext
from amora.cognition.providers_structured import (
    _REASONING_ASSIST_TIMEOUT_SECONDS,
    _needs_reasoning_support,
)
from amora.events import PrivacyScope


def _context(text: str, *, depth: str, act: str = "answer_question") -> CognitiveContext:
    return CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime.now(UTC),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text=text,
        dialogue_plan={
            "act": act,
            "depth": depth,
            "response_moves": ["answer_question"],
            "ask_followup": False,
            "offer_help": False,
            "closing_mode": "statement",
        },
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
    )


def test_medium_technical_chat_stays_on_single_surface_model_call() -> None:
    context = _context(
        "Esse bug no servidor parece estranho, o que você acha?",
        depth="medium",
    )
    assert _needs_reasoning_support(context) is False


def test_long_nontechnical_conversation_does_not_trigger_reasoning_specialist() -> None:
    context = _context(
        (
            "Eu tava pensando bastante na forma como a gente conversa e queria saber "
            "o que você acha de tudo isso quando lembra do assunto anterior e do jeito "
            "que a conversa foi mudando entre a gente durante o dia."
        ),
        depth="deep",
    )
    assert _needs_reasoning_support(context) is False


def test_deep_genuinely_technical_turn_can_still_use_reasoning_specialist() -> None:
    context = _context(
        "Me explica em detalhe como corrigir esse bug de concorrência na arquitetura do servidor Kotlin.",
        depth="deep",
    )
    assert _needs_reasoning_support(context) is True


def test_optional_specialist_timeout_is_bounded_for_realtime_dialogue() -> None:
    assert 0 < _REASONING_ASSIST_TIMEOUT_SECONDS <= 6.0
