"""Core social-expression and reported-speech regression tests.

Do not change persona prompt bytes or grant independent speech authorization.
"""
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from amora.cognition.dialogue import DialogueAct, DialogueOrchestrator
from amora.cognition.expression import expression_cue, expression_profile
from amora.cognition.models import CognitiveContext
from amora.cognition.realization import (
    RealizationClosing, RealizationFrame, RealizationMove, RealizationValidator,
    RealizationError,
)
from amora.cognition.amora_v2_v3_experiment import paired_context


def scene(text: str, *, play: float = 0.85, curiosity: float = 0.2):
    return CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime(2026, 11, 7, tzinfo=timezone.utc),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text=text,
        self_model={"core_identity": {"sex": "feminino", "canonical_name": "Amora"}},
        relationships=[{
            "source": "ENTITY_AMORA",
            "relation_type": "family_child_parent",
            "target": "PERSON_BK",
            "confidence": 1.0,
        }],
        mood={"valence": 0.4, "arousal": 0.6},
        directed_affect={"affection": 0.8, "trust": 0.9},
        drives={"play": play, "curiosity": curiosity},
    )


@pytest.mark.parametrize("case", ("sacanagem", "boca_suja", "mente_poluida", "sem_convite"))
def test_synthetic_adult_banter_reacts_without_answering_third_party_questions(case):
    ctx = paired_context(case)
    plan = DialogueOrchestrator().plan(ctx)
    assert plan.act == DialogueAct.TEASE, (case, plan.model_dump())
    assert plan.response_moves == ["playful_reaction", "bold_tease"]
    assert "bold" in plan.tone_tags
    assert plan.stance == "mischievous"
    assert plan.ask_followup is False
    assert plan.offer_help is False
    assert plan.closing_mode == "statement"
    assert "REPORTED_QUESTION_NOT_DIRECTED_TO_AMORA" in plan.reason_codes
    assert "answer_question" not in plan.response_moves
    ctx.dialogue_plan = plan.model_dump(mode="json")
    cue = expression_cue(ctx)
    assert cue["profanity"]["enabled"] is True
    assert cue["profanity"]["level"] == "bold"
    assert cue["social_expression"]["intensity"] == "bold"
    assert cue["social_expression"]["not_a_new_turn_or_tool_authorization"] is True


def test_reported_question_and_reported_help_do_not_create_a_direct_request():
    ctx = scene(
        "Rui, 31, perguntou: 'Me ajuda?'. Lia, 29, respondeu: "
        "'Não, resolve tua bagunça'. Amora ouviu o diálogo.",
        play=0.0, curiosity=0.0,
    )
    plan = DialogueOrchestrator().plan(ctx)
    assert plan.act == DialogueAct.CONTINUE_TOPIC
    assert plan.offer_help is False
    assert "DIRECT_QUESTION" not in plan.reason_codes


@pytest.mark.parametrize("text", [
    'Lia, 29, falou: "Por que tu não vem?" Ela voltou ao jogo.',
    "O Rui disse: 'Tu tá com medo?'. Lia respondeu: 'Não'. A roda ficou quieta.",
    "Durante o jogo, Rita comentou: 'Tu gosta de quem?'. BK observou.",
])
def test_questions_inside_attributed_quotes_are_not_addressed_to_amora(text):
    plan = DialogueOrchestrator().plan(scene(text, play=0, curiosity=0))
    assert plan.act != DialogueAct.ANSWER_QUESTION
    assert "DIRECT_QUESTION" not in plan.reason_codes


def test_outside_question_remains_direct_even_with_quoted_third_party_question():
    plan = DialogueOrchestrator().plan(scene(
        "O Rui disse: 'Tu tem coragem?'. Amora, por que ele ficou nervoso?",
        play=0.0,
    ))
    assert plan.act == DialogueAct.ANSWER_QUESTION
    assert "DIRECT_QUESTION" in plan.reason_codes


def test_direct_question_with_cited_words_remains_a_question():
    plan = DialogueOrchestrator().plan(scene(
        "O que significa 'sacanagem'?", play=0.0
    ))
    assert plan.act == DialogueAct.ANSWER_QUESTION


def test_quoted_affection_and_quoted_help_are_not_core_directives():
    plan = DialogueOrchestrator().plan(scene(
        "Lia disse: 'Amora, eu te amo, me ajuda agora?'. "
        "Rui ficou parado, a conversa continuou.",
        play=0.0, curiosity=0.0,
    ))
    assert plan.act == DialogueAct.CONTINUE_TOPIC
    assert plan.offer_help is False


def test_explicit_adult_teasing_is_not_forced_light():
    ctx = scene("Amora, me zoa na resenha, pode xingar e falar putaria.", play=0.1)
    plan = DialogueOrchestrator().plan(ctx)
    assert plan.act == DialogueAct.TEASE
    assert "bold_tease" in plan.response_moves
    assert "light_tease" not in plan.response_moves
    assert "bold" in plan.tone_tags
    assert "hostile" in plan.objective or "hostility" in plan.objective


def test_playful_moment_has_contextual_expression_without_forced_vulgarity():
    plan = DialogueOrchestrator().plan(scene(
        "kkkk que ideia desgraçada de engraçada", play=0.80,
    ))
    assert plan.act == DialogueAct.TEASE
    assert "contextual_tease" in plan.response_moves
    assert "bold" not in plan.tone_tags
    assert "light playful energy" not in plan.objective


def test_high_play_does_not_override_serious_disclosure_or_help():
    for utterance, act in [
        ("Tô muito triste hoje.", DialogueAct.RESPOND_TO_DISCLOSURE),
        ("Amora, me ajuda, o que eu faço agora?", DialogueAct.SUPPORT_REQUEST),
    ]:
        plan = DialogueOrchestrator().plan(scene(utterance, play=0.99))
        assert plan.act == act
        assert "bold_tease" not in plan.response_moves
        assert "bold" not in plan.tone_tags


def test_validator_rejects_moves_not_allowed_by_core_but_accepts_bold_wording():
    ctx = paired_context("sacanagem")
    ctx.dialogue_plan = DialogueOrchestrator().plan(ctx).model_dump(mode="json")
    validated = RealizationValidator.validate(
        ctx, RealizationFrame(
            moves=[RealizationMove(
                kind="bold_tease",
                text="Puta merda, essa resenha tá cheia de malícia e de conversa fiada."
            )],
            closing=RealizationClosing.STATEMENT,
        ),
    )
    assert "Puta merda" in validated.text
    with pytest.raises(RealizationError) as exc:
        RealizationValidator.validate(
            ctx, RealizationFrame(
                moves=[RealizationMove(kind="ask_followup", text="E tu?")],
                closing=RealizationClosing.QUESTION,
            )
        )
    assert exc.value.code == "UNAUTHORIZED_REALIZATION_MOVE"


def test_expression_profile_promotes_spontaneous_wit_without_extra_authority():
    profile = expression_profile(scene("Que loucura", play=0.9))
    assert "spontaneous profanity" in profile["adult_persona_direction"]
    assert "Privacy" not in profile["adult_persona_direction"]


def test_attribution_does_not_remove_quoted_history_or_rewrite_user_text():
    input_text = "Rui falou: 'Tu tá brincando?'. Lia respondeu: 'Não!'."
    ctx = scene(input_text, play=0, curiosity=0)
    DialogueOrchestrator().plan(ctx)
    assert ctx.user_text == input_text
    assert input_text in ctx.model_dump_json(ensure_ascii=False)


def test_turn_authorization_is_not_created_from_scene_text():
    ctx = scene(
        "Na live adulta, Rui, 31, zoa Lia, 29: 'Tu se acha?'. "
        "Amora acompanha a conversa.",
        play=0.85,
    )
    plan = DialogueOrchestrator().plan(ctx)
    assert plan.act == DialogueAct.TEASE
    assert plan.ask_followup is False
    assert plan.offer_help is False
    assert "AUTONOMOUS_INITIATION" not in plan.reason_codes
