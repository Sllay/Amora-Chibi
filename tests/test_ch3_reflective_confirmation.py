from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from amora.cognition.dialogue import DialogueAct, DialogueOrchestrator
from amora.cognition.models import CognitiveContext
from amora.cognition.realization import (
    RealizationError,
    RealizationValidator,
    realization_instruction,
)


ADULT_RELATIONSHIP_TURN = (
    "Tô meio confuso. Não é só sobre sexo ou tesão, é sobre a mudança "
    "de carinho depois. Tu entendeu o que eu quis dizer?"
)


def _context(text: str, *, history: bool = True) -> CognitiveContext:
    context = CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime(2026, 11, 4, tzinfo=timezone.utc),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text=text,
        self_model={"core_identity": {"canonical_name": "Amora"}},
        recent_messages=(
            [
                {
                    "turn_id": str(uuid4()),
                    "speaker_id": "PERSON_BK",
                    "addressee_id": "ENTITY_AMORA",
                    "text": "Antes de ficar com a pessoa adulta, a gente conversava mais.",
                },
                {
                    "turn_id": str(uuid4()),
                    "speaker_id": "ENTITY_AMORA",
                    "addressee_id": "PERSON_BK",
                    "text": "Depois do encontro mudou o jeito de conversar?",
                },
            ] if history else []
        ),
        remote_processing_allowed=True,
    )
    context.dialogue_plan = DialogueOrchestrator().plan(context).model_dump(mode="json")
    return context


def _realization(context: CognitiveContext, text: str, *, closing: str = "statement") -> dict:
    return {
        "moves": [{
            "kind": context.dialogue_plan["response_moves"][0],
            "text": text,
        }],
        "closing": closing,
    }


def test_contextual_emotional_disclosure_allows_optional_reflective_confirmation() -> None:
    ctx = _context(ADULT_RELATIONSHIP_TURN)
    plan = ctx.dialogue_plan
    assert plan["act"] == DialogueAct.RESPOND_TO_DISCLOSURE.value
    assert plan["depth"] == "medium"
    assert plan["ask_followup"] is False
    assert plan["allow_reflective_question"] is True
    assert plan["closing_mode"] == "natural"
    assert "REFLECTIVE_CONFIRMATION_INVITED" in plan["reason_codes"]

    validated = RealizationValidator.parse_and_validate(
        ctx,
        _realization(
            ctx,
            "Parece que é a mudança de carinho depois do encontro que te pegou, né?",
            closing="question",
        ),
    )
    assert validated.text.endswith("?")
    assert "REFLECTIVE_CONFIRMATION_AUTHORIZED" in validated.reason_codes

    statement = RealizationValidator.parse_and_validate(
        ctx, _realization(
            ctx, "Tu sentiu que o carinho mudou depois de vocês ficarem.",
            closing="statement",
        )
    )
    assert statement.text.endswith(".")
    assert "REFLECTIVE_CONFIRMATION_AUTHORIZED" not in statement.reason_codes
    instruction = realization_instruction(ctx)
    assert "UMA pergunta breve para confirmar" in instruction
    assert "não crie move ask_followup" in instruction


def test_reflective_confirmation_can_be_marked_statement_by_provider() -> None:
    ctx = _context(ADULT_RELATIONSHIP_TURN)
    result = RealizationValidator.parse_and_validate(
        ctx,
        _realization(ctx, "É essa mudança no carinho que tá incomodando?", closing="statement"),
    )
    assert result.text.endswith("?")
    assert "REFLECTIVE_CONFIRMATION_AUTHORIZED" in result.reason_codes


@pytest.mark.parametrize(
    ("text", "history"),
    [
        ("Tô meio cansado hoje.", True),
        ("Tô meio confuso, mas não te perguntei nada.", True),
        (ADULT_RELATIONSHIP_TURN, False),
        ("Qual é a capital do Brasil?", True),
        ("Amora, me xinga na brincadeira kkkkk.", True),
    ]
)
def test_ordinary_turns_reject_uninvited_followup_question(text: str, history: bool) -> None:
    ctx = _context(text, history=history)
    assert ctx.dialogue_plan["allow_reflective_question"] is False
    with pytest.raises(RealizationError) as exc:
        RealizationValidator.parse_and_validate(
            ctx, _realization(ctx, "Quer que eu te faça outra pergunta?"),
        )
    assert exc.value.code == "FOLLOWUP_NOT_AUTHORIZED"


def test_reflective_permission_never_authorizes_question_spam_or_new_moves() -> None:
    ctx = _context(ADULT_RELATIONSHIP_TURN)

    for text in (
        "É sobre o carinho? Quer me contar mais?",
        "Quer mais alguma coisa? Ou prefere falar depois?",
    ):
        with pytest.raises(RealizationError) as exc:
            RealizationValidator.parse_and_validate(ctx, _realization(ctx, text))
        assert exc.value.code == "FOLLOWUP_NOT_AUTHORIZED"

    with pytest.raises(RealizationError) as exc:
        RealizationValidator.parse_and_validate(
            ctx, _realization(ctx, "Eu entendi.", closing="question"),
        )
    assert exc.value.code == "FOLLOWUP_NOT_AUTHORIZED"

    with pytest.raises(RealizationError) as exc:
        RealizationValidator.parse_and_validate(
            ctx,
            {
                "moves": [{"kind": "ask_followup", "text": "Conta mais?"}],
                "closing": "question",
            },
        )
    assert exc.value.code == "UNAUTHORIZED_REALIZATION_MOVE"


def test_non_disclosure_confirmation_question_is_narrow_and_context_scoped() -> None:
    ctx = _context("Amora, tu entendeu o que eu quis dizer?", history=True)
    plan = ctx.dialogue_plan
    assert plan["act"] == DialogueAct.ANSWER_QUESTION.value
    assert plan["allow_reflective_question"] is True
    assert plan["ask_followup"] is False
    assert plan["closing_mode"] == "natural"

    accepted = RealizationValidator.parse_and_validate(
        ctx,
        _realization(ctx, "Tu tá falando da distância depois do encontro, é isso?", closing="question"),
    )
    assert accepted.text.endswith("?")
