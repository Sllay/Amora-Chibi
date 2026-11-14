import pytest

from amora.cognition.context import ContextCompiler
from amora.cognition.context_budget import estimate_context
from amora.cognition.dialogue import DialogueOrchestrator
from amora.cognition.prompt_frame import render_current_turn, render_trusted_protocol
from test_phase2_context_contracts import context, messages


def legacy_final_pass(value):
    """The exact final replan removed from the default compiler."""
    identity = value.dialogue_plan.get("speaker_identity")
    old = set(value.dialogue_plan.get("reason_codes") or ())
    value.reason_codes = [code for code in value.reason_codes
                          if code not in old and not code.startswith("DIALOGUE_ACT_")]
    value.dialogue_plan = DialogueOrchestrator().plan(value).model_dump(mode="json")
    value.reason_codes.extend(value.dialogue_plan.get("reason_codes") or ())
    value.reason_codes.append("DIALOGUE_ACT_" + value.dialogue_plan["act"].upper())
    if identity:
        value.dialogue_plan["speaker_identity"] = identity
    if value.acir is not None:
        value.dialogue_plan["input_acir"] = value.acir
    return value


@pytest.mark.parametrize("text,mode", [
    ("Tu entendeu o que eu disse?", None),
    ("Qual seu nome?", None),
    ("Minha amiga falou com a sua mãe. Quem falou com quem?", None),
    ("O que tu acha de programar um jogo?", None),
    ("Objetivo interno", "AUTONOMOUS_INITIATION"),
    ("Reflexão sintética", "COGNITIVE_REFLECTION"),
])
@pytest.mark.parametrize("trim", [False, True])
def test_default_budget_fit_exactly_matches_legacy_final_pass(text, mode, trim):
    value = context(text, recent_messages=messages(exchanges=5, chars=700))
    value.dialogue_plan["speaker_identity"] = {
        "entity_id": "PERSON_BK", "canonical_name": "BK", "entity_type": "person"}
    value.acir = {"speaker": "PERSON_BK", "addressee": "ENTITY_AMORA", "intent": "communicate"}
    if mode:
        value.reason_codes.append(mode)
    if trim:
        minimal = value.model_copy(deep=True)
        minimal.recent_messages = minimal.recent_messages[-2:]
        minimal = ContextCompiler._fit_budget(minimal)
        value.token_budget = estimate_context(minimal) + 120
    fitted = ContextCompiler._fit_budget(value)
    if trim:
        assert len(fitted.recent_messages) < 10
    legacy = legacy_final_pass(fitted.model_copy(deep=True))
    assert fitted.model_dump(mode="json") == legacy.model_dump(mode="json")
    assert render_current_turn(fitted) == render_current_turn(legacy)
    assert render_trusted_protocol(fitted) == render_trusted_protocol(legacy)


def test_default_planner_runs_once_when_budget_fits(monkeypatch):
    value = context()
    calls = 0
    original = DialogueOrchestrator.plan

    def counted(self, ctx):
        nonlocal calls
        calls += 1
        return original(self, ctx)

    monkeypatch.setattr(DialogueOrchestrator, "plan", counted)
    ContextCompiler._fit_budget(value, DialogueOrchestrator())
    assert calls == 1


def test_custom_planner_retains_legacy_final_pass():
    class CustomPlanner(DialogueOrchestrator):
        calls = 0

        def plan(self, ctx):
            self.calls += 1
            return super().plan(ctx)

    planner = CustomPlanner()
    ContextCompiler._fit_budget(context(), planner)
    assert planner.calls == 2
