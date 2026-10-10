from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from amora.cognition.dialogue import DialogueAct, DialogueOrchestrator, FactStatus
from amora.cognition.models import CognitiveContext


def _context(text: str, *, self_model=None, drives=None, recent=None) -> CognitiveContext:
    return CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime(2026, 10, 10, tzinfo=timezone.utc),
        speaker_id="SESSION_ANDROID_SPEAKER_1",
        target_id="ENTITY_AMORA",
        user_text=text,
        self_model=self_model or {"core_identity": {"canonical_name": "Amora"}},
        relationships=[
            {
                "source": "ENTITY_AMORA",
                "relation_type": "family_child_parent",
                "target": "SESSION_ANDROID_SPEAKER_1",
                "confidence": 1.0,
            }
        ],
        mood={"valence": 0.1, "arousal": 0.4, "dominance": 0.5},
        directed_affect={"affection": 0.8, "trust": 0.9, "gratitude": 0.2},
        drives=drives or {},
        recent_messages=recent or [],
        remote_processing_allowed=True,
    )


def test_age_question_uses_canonical_self_fact_and_does_not_invent_missing_value() -> None:
    plan = DialogueOrchestrator().plan(_context("Quantos anos você tem, Amora?"))

    assert plan.act == DialogueAct.ANSWER_SELF_FACT
    assert plan.fact_key == "age"
    assert plan.fact_status == FactStatus.MISSING
    assert plan.fact_value is None
    assert plan.ask_followup is False
    assert plan.offer_help is False
    assert "SELF_FACT_UNDEFINED" in plan.reason_codes


def test_age_can_be_derived_locally_from_canonical_birth_date() -> None:
    plan = DialogueOrchestrator().plan(
        _context(
            "Qual a sua idade?",
            self_model={
                "core_identity": {
                    "canonical_name": "Amora",
                    "birth_date": "2024-10-09",
                }
            },
        )
    )

    assert plan.fact_status == FactStatus.KNOWN
    assert plan.fact_value == 2


def test_personal_disclosure_prefers_presence_over_unsolicited_help() -> None:
    plan = DialogueOrchestrator().plan(_context("Tô meio cansado hoje."))

    assert plan.act == DialogueAct.RESPOND_TO_DISCLOSURE
    assert plan.response_moves == ["acknowledge_disclosure", "share_relational_reaction"]
    assert plan.offer_help is False
    assert plan.ask_followup is False
    assert plan.style_register == "intimate_colloquial"


def test_explicit_help_request_allows_help_semantically() -> None:
    plan = DialogueOrchestrator().plan(_context("Amora, me ajuda, o que eu faço agora?"))

    assert plan.act == DialogueAct.SUPPORT_REQUEST
    assert plan.offer_help is True
    assert plan.ask_followup is False


def test_bare_name_call_is_acknowledgement_not_assistant_offer() -> None:
    plan = DialogueOrchestrator().plan(_context("Amora."))

    assert plan.act == DialogueAct.ACKNOWLEDGE_CALL
    assert plan.response_moves == ["acknowledge_call"]
    assert plan.offer_help is False
    assert plan.ask_followup is False


def test_curiosity_may_create_followup_only_for_topic_continuation() -> None:
    plan = DialogueOrchestrator().plan(
        _context("Hoje foi uma correria danada", drives={"curiosity": 0.9})
    )
    assert plan.act == DialogueAct.CONTINUE_TOPIC
    assert plan.ask_followup is True

    previous_question = [
        {
            "turn_id": str(uuid4()),
            "speaker_id": "ENTITY_AMORA",
            "text": "E depois, o que aconteceu?",
        }
    ]
    plan_after_question = DialogueOrchestrator().plan(
        _context(
            "Aí eu resolvi aquilo",
            drives={"curiosity": 0.9},
            recent=previous_question,
        )
    )
    assert plan_after_question.ask_followup is False
