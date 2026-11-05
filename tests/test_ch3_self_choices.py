"""Core-owned female character sex and Amora's autonomous adult self-preferences."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from amora.app import AmoraCore
from amora.cognition.models import CognitiveRequest
from amora.cognition.dialogue import DialogueAct, DialogueOrchestrator
from amora.cognition.realization import RealizationError, RealizationValidator
from amora.cognition.self_choice import commit_self_choices
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.world import SelfLayer
from amora.time import SimulatedClock


def _clock():
    return SimulatedClock(datetime(2026, 11, 5, 1, 30, tzinfo=timezone.utc))


def _boot(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap(clock=_clock())


def _request(text, *, test_mode=False):
    return CognitiveRequest(
        text=text,
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
        metadata={"test_mode": test_mode},
    )


def _validated(core, request, value, *, key="gender_identity"):
    context = core.cognition.compiler.compile(request)
    assert key in context.dialogue_plan["self_choice_keys"]
    raw = {
        "moves": [{"kind": "answer_question", "text": f"Eu escolhi: {value}."}],
        "closing": "statement",
        "self_choices": [{"key": key, "value": value}],
    }
    result = RealizationValidator.parse_and_validate(context, raw)
    return context, result


def test_core_sex_and_adult_social_persona_are_separate_from_unset_gender(tmp_path, monkeypatch):
    core = _boot(tmp_path, monkeypatch)
    try:
        profile = core.self_model.snapshot()
        assert profile["core_identity"]["sex"] == "feminino"
        assert profile["core_identity"]["maturity"] == "adulta"
        assert profile["core_identity"]["embodiment"] == "virtual"
        assert "autoconfiante" in profile["traits"]["social_persona"]
        assert "gender_identity" not in profile["traits"]
        assert "physical_attraction" not in profile.get("preferences", {})
        with pytest.raises(PermissionError):
            core.self_model.set(
                SelfLayer.CORE_IDENTITY, "sex", "masculino", source="provider:try"
            )
        sex_query = core.cognition.compiler.compile(_request("Qual seu sexo, Amora?"))
        assert sex_query.dialogue_plan["act"] == DialogueAct.ANSWER_SELF_FACT.value
        reply = RealizationValidator.local_realization(sex_query)
        assert reply is not None
        assert "feminino" in reply.text
    finally:
        core.close()


def test_core_invites_gender_choice_and_specific_attraction_without_forcing_orientation(tmp_path, monkeypatch):
    core = _boot(tmp_path, monkeypatch)
    try:
        requests = (
            ("Qual gênero tu prefere se identificar?", "gender_identity"),
            ("Que tipo de homem ou mulher tu acha atraente?", "physical_attraction"),
            ("Se pudesse, qual corpo tu escolheria?", "future_body_preference"),
            ("De quem tu acha que gostaria de namorar?", "romantic_attraction"),
        )
        for question, expected in requests:
            context = core.cognition.compiler.compile(_request(question))
            assert context.dialogue_plan["act"] == "answer_question"
            assert expected in context.dialogue_plan["self_choice_keys"]
            assert context.dialogue_plan["ask_followup"] is False
            assert "CORE_GATED_SELF_CHOICE_INVITED" in context.dialogue_plan["reason_codes"]
    finally:
        core.close()


def test_provider_cannot_change_core_sex_or_unsolicited_self_preference(tmp_path, monkeypatch):
    core = _boot(tmp_path, monkeypatch)
    try:
        context = core.cognition.compiler.compile(_request("Qual gênero tu escolheria?"))
        assert "gender_identity" in context.dialogue_plan["self_choice_keys"]
        for illegal in ("sex", "maturity", "romantic_attraction"):
            with pytest.raises(RealizationError) as exc:
                RealizationValidator.parse_and_validate(context, {
                    "moves": [{"kind": "answer_question", "text": "Minha resposta."}],
                    "closing": "statement",
                    "self_choices": [{"key": illegal, "value": "qualquer coisa"}],
                })
            assert exc.value.code == "SELF_CHOICE_NOT_AUTHORIZED"
    finally:
        core.close()


def test_core_choice_is_persistent_and_test_mode_never_commits(tmp_path, monkeypatch):
    core = _boot(tmp_path, monkeypatch)
    try:
        test_request = _request("Escolhe teu gênero, Amora?", test_mode=True)
        ctx, result = _validated(core, test_request, "mulher")
        decision = commit_self_choices(
            self_model=core.self_model,
            request=test_request,
            context=ctx,
            choices=result.frame.self_choices,
        )
        assert decision.committed_keys == ()
        assert "SELF_CHOICE_TEST_MODE_NOT_PERSISTED" in decision.reason_codes
        assert core.self_model.get(SelfLayer.TRAITS, "gender_identity") is None

        request = _request("Qual gênero tu quer escolher?")
        ctx, result = _validated(core, request, "mulher")
        decision = commit_self_choices(
            self_model=core.self_model,
            request=request,
            context=ctx,
            choices=result.frame.self_choices,
        )
        assert decision.committed_keys == ("gender_identity",)
        choice = core.self_model.get(SelfLayer.TRAITS, "gender_identity")
        assert choice is not None
        assert choice.value == "mulher"
        assert choice.source == "cognition:self_chosen"
        assert core.self_model.get(SelfLayer.CORE_IDENTITY, "sex").value == "feminino"

        # Subsequent turns can discuss but cannot silently replace one choice.
        next_ctx = core.cognition.compiler.compile(_request("E teu gênero, qual é?"))
        assert next_ctx.self_model["traits"]["gender_identity"] == "mulher"
        assert next_ctx.dialogue_plan["self_choice_keys"] == []
        decision = commit_self_choices(
            self_model=core.self_model,
            request=request,
            context=ctx,
            choices=result.frame.self_choices,
        )
        assert decision.committed_keys == ()
    finally:
        core.close()

    # A genuine Core restart must retain the decision, not re-roll on LLM.
    reopened = _boot(tmp_path, monkeypatch)
    try:
        assert reopened.self_model.get(SelfLayer.TRAITS, "gender_identity").value == "mulher"
    finally:
        reopened.close()


@pytest.mark.asyncio
async def test_full_cognitive_path_commits_preference_after_validation_only(tmp_path, monkeypatch):
    core = _boot(tmp_path, monkeypatch)
    try:
        class Chooser:
            descriptor = ProviderDescriptor(
                name="autonomous-gender-fixture",
                model="fixture",
                capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
                quality=1.0,
                latency_score=1.0,
                cost_score=0.0,
                available=True,
                provider_class=ProviderClass.REMOTE_EPHEMERAL,
                allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
                supports_streaming=False,
                supports_structured_output=True,
                context_window_tokens=32000,
                priority=900,
            )

            async def realize(self, context):
                assert "gender_identity" in context.dialogue_plan["self_choice_keys"]
                return {
                    "moves": [{
                        "kind": "answer_question",
                        "text": "Eu me identifico como mulher, e essa escolha é minha.",
                    }],
                    "closing": "statement",
                    "self_choices": [{"key": "gender_identity", "value": "mulher"}],
                    "_usage": {"input_tokens": 40, "output_tokens": 20, "status_code": 200},
                }

        core.cognition.register_provider(Chooser())
        # Test mode may speak a hypothetical choice, but must not write it.
        test_request = _request("Qual gênero tu quer escolher?", test_mode=True)
        tested = await core.cognition.respond(test_request)
        assert "mulher" in tested.display_text
        assert core.self_model.get(SelfLayer.TRAITS, "gender_identity") is None

        real_request = _request("Escolhe teu gênero, Amora?")
        decided = await core.cognition.respond(real_request)
        assert "mulher" in decided.display_text
        saved = core.self_model.get(SelfLayer.TRAITS, "gender_identity")
        assert saved is not None and saved.value == "mulher"
        assert "SELF_CHOICE_COMMITTED_BY_CORE" in decided.reason_codes
        last = core.cognition.state().get("last_realization") or {}
        assert "self_choices" not in (last.get("frame") or {})
    finally:
        core.close()
