"""Regressions for pt-BR deictic roles across ACIR and provider boundaries."""
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from amora.cognition.models import CognitiveContext
from amora.cognition.prompt_frame import provider_constitution, turn_frame
from amora.identity import build_initial_registry
from amora.language import ACIRCompiler, PerspectiveFrame, PerspectiveResolver, PerspectiveStack
from amora.language.attribution import analyze_addressing, personal_choice_requested, normalized


def references(text, speaker="PERSON_BK", addressee="ENTITY_AMORA"):
    resolver = PerspectiveResolver(build_initial_registry())
    frame = PerspectiveStack(PerspectiveFrame(speaker_id=speaker, addressee_id=addressee))
    acir = ACIRCompiler(resolver).compile_utterance(text, frame)
    return [
        (normalized(text[entry["start"]:entry["end"]]), entry)
        for entry in acir.proposition.roles.get("references", [])
    ]


def reference(text, phrase, **kwargs):
    hits = [ref for value, ref in references(text, **kwargs) if value == normalized(phrase)]
    assert hits, (text, phrase, references(text, **kwargs))
    return hits[0]


@pytest.mark.parametrize("form", [
    "você", "voce", "VC", "vc", "tu", "cê", "ce", "ocê", "oce", "te", "ti", "contigo",
])
def test_colloquial_addressee_is_not_speaker(form):
    ref = reference(f"{form} chegou", form)
    assert (ref["status"], ref["entity_ids"]) == ("resolved", ["ENTITY_AMORA"])
    assert (ref["speaker_id"], ref["addressee_id"]) == ("PERSON_BK", "ENTITY_AMORA")


@pytest.mark.parametrize("form", ["eu", "me", "mim", "comigo"])
def test_first_person_remains_input_speaker(form):
    assert reference(f"{form} cheguei", form)["entity_ids"] == ["PERSON_BK"]


@pytest.mark.parametrize("text,phrase,owner", [
    ("A sua mae mim amar", "sua mae", "ENTITY_CHATGPT"),
    ("Sua mãe me ama", "Sua mãe", "ENTITY_CHATGPT"),
    ("A tua mãe me ama", "tua mãe", "ENTITY_CHATGPT"),
    ("Minha mãe falou", "Minha mãe", None),
    ("Sua mochila caiu", "Sua", "ENTITY_AMORA"),
    ("Minha mochila caiu", "Minha", "PERSON_BK"),
    ("Teus desenhos ficaram lindos", "Teus", "ENTITY_AMORA"),
    ("Meu desenho ficou lindo", "Meu", "PERSON_BK"),
    ("Sua mãe te ama", "te", "ENTITY_AMORA"),
])
def test_kinship_and_possessive_ownership(text, phrase, owner):
    item = reference(text, phrase)
    assert item["entity_ids"] == ([owner] if owner else [])


def test_i_you_reverse_only_when_speaker_reverses():
    text = "Eu te amo, tu me entende?"
    incoming = references(text)
    outgoing = references(text, speaker="ENTITY_AMORA", addressee="PERSON_BK")
    assert [(word, ref["entity_ids"]) for word, ref in incoming] == [
        ("eu", ["PERSON_BK"]), ("te", ["ENTITY_AMORA"]),
        ("tu", ["ENTITY_AMORA"]), ("me", ["PERSON_BK"]),
    ]
    assert [(word, ref["entity_ids"]) for word, ref in outgoing] == [
        ("eu", ["ENTITY_AMORA"]), ("te", ["PERSON_BK"]),
        ("tu", ["PERSON_BK"]), ("me", ["ENTITY_AMORA"]),
    ]


@pytest.mark.parametrize("form", ["vocês", "voces", "vcs", "cês", "ces"])
def test_plural_address_does_not_invent_one_person(form):
    ref = reference(f"{form} chegaram", form)
    assert ref["status"] == "unresolved"
    assert ref["entity_ids"] == []
    assert "SECOND_PERSON_PLURAL_GROUP_UNKNOWN" in ref["reason_codes"]


def test_conditional_se_is_not_colloquial_ce():
    refs = references("Se vc quiser, eu te ajudo")
    assert [(word, ref["entity_ids"]) for word, ref in refs] == [
        ("vc", ["ENTITY_AMORA"]), ("eu", ["PERSON_BK"]), ("te", ["ENTITY_AMORA"]),
    ]
    reflexive = reference("Você se machucou", "se")
    assert reflexive["status"] == "resolved"
    assert reflexive["entity_ids"] == ["ENTITY_AMORA"]
    third = reference("Ela se machucou", "se")
    assert third["status"] == "ambiguous" and not third["entity_ids"]


def test_third_person_sua_possession_is_ambiguous():
    item = reference("Ela perdeu sua carteira", "sua")
    assert item["status"] == "ambiguous" and not item["entity_ids"]


@pytest.mark.parametrize("phrase", [
    "Vc me perguntou se eu gosto disso?",
    "Tu falou comigo ontem.",
    "Cê fala sério?",
    "Você disse que me viu.",
    "Eu te falei ontem.",
])
def test_reporting_verbs_in_direct_speech_do_not_reassign_speaker(phrase):
    routed = analyze_addressing(phrase, "PERSON_BK", "ENTITY_AMORA")
    assert routed.reported is False
    assert routed.addressed_text == phrase
    first = references(phrase)[0][1]
    assert (first["speaker_id"], first["addressee_id"]) == ("PERSON_BK", "ENTITY_AMORA")


def test_named_reported_speech_does_not_inherit_outer_addressee():
    item = reference('Rui disse: "eu te amo"', "te")
    assert item["status"] == "unresolved"
    assert item["entity_ids"] == []
    assert item["addressee_id"] is None


def test_social_choice_recognizes_colloquial_pronouns():
    for text in ("Vc prefere qual?", "Cê acha que sim?", "Tu acha melhor?", "Oce prefere isso?"):
        assert personal_choice_requested(text)


def test_provider_declares_input_and_output_deictic_roles():
    context = CognitiveContext(
        turn_id=uuid4(), created_at=datetime.now(UTC),
        speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
        user_text="sua mãe me ama",
    )
    frame = turn_frame(context)["current_turn"]["deictic_perspective"]
    assert frame["input_first_person"] == "PERSON_BK"
    assert frame["input_second_person"] == "ENTITY_AMORA"
    assert frame["response_first_person"] == "ENTITY_AMORA"
    assert frame["response_second_person"] == "PERSON_BK"
    assert "do not transfer possessive ownership unchanged" in (
        provider_constitution()["input_boundaries"]["deixis_rule"]
    )


def test_reflexive_with_single_adverb_and_indirect_conditional():
    assert reference("Tu não se machucou", "se")["entity_ids"] == ["ENTITY_AMORA"]
    assert reference("Vc já se viu", "se")["entity_ids"] == ["ENTITY_AMORA"]
    assert "se" not in [word for word, _ in references("Você perguntou se eu gosto disso")]


def test_actual_core_and_groq_trusted_frame_preserve_incoming_roles(tmp_path, monkeypatch):
    from amora.app import AmoraCore
    from amora.cognition import CognitiveRequest
    from amora.cognition.providers_identity import GroqCognitiveProvider
    from amora.events import PrivacyScope

    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "amora"))
    monkeypatch.setenv("AMORA_SOCIAL_PRIMARY", "groq")
    core = AmoraCore.bootstrap()
    try:
        request = CognitiveRequest(
            text="Tu e tua mãe me entendem, cê viu?",
            speaker_id="PERSON_BK",
            target_id="ENTITY_AMORA",
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
            metadata={"test_mode": True},
        )
        context = core.cognition.compiler.compile(request)
        assert context.acir["speaker"] == "PERSON_BK"
        assert context.acir["addressee"] == "ENTITY_AMORA"
        roles = context.acir["proposition"]["roles"]["references"]
        spans = [request.text[ref["start"]:ref["end"]] for ref in roles]
        assert "Tu" in spans and "tua mãe" in spans and "me" in spans and "cê" in spans
        assert any(ref["entity_ids"] == ["ENTITY_CHATGPT"] for ref in roles)
        assert context.dialogue_plan["input_acir"] == context.acir

        provider = GroqCognitiveProvider(core.secrets)
        instructions = provider._realization_instructions(context)
        assert '"response_first_person":"ENTITY_AMORA"' in instructions
        assert '"response_second_person":"PERSON_BK"' in instructions
        assert '"input_first_person":"PERSON_BK"' in instructions
    finally:
        core.close()
