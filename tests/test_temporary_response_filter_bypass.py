"""Temporary Core response-filter bypass must not alter words or canonical state."""

from __future__ import annotations

from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.cognition.models import CognitiveRequest
from amora.cognition.realization import (
    RealizationError,
    RealizationValidator,
    response_semantic_filter_enabled,
)
from amora.events import PrivacyScope
from test_v10_dialogue_emotion_integration import StructuredProvider, _clock, _context


def test_semantic_filter_is_off_by_default_and_can_be_restored(monkeypatch) -> None:
    monkeypatch.delenv("AMORA_RESPONSE_SEMANTIC_FILTER_ENABLED", raising=False)
    assert response_semantic_filter_enabled() is False
    monkeypatch.setenv("AMORA_RESPONSE_SEMANTIC_FILTER_ENABLED", "1")
    assert response_semantic_filter_enabled() is True


def test_disabled_filter_preserves_reply_even_when_plan_forbids_move_and_question() -> None:
    text = "Porra, eu achei isso engraçado demais! Tu achou também?"
    context = _context(ask_followup=False)
    result = RealizationValidator.parse_and_validate(
        context,
        {
            "moves": [{"kind": "offer_help", "text": text}],
            "closing": "question",
        },
        enforce_dialogue_plan=False,
    )
    assert result.text == text
    assert result.frame.moves[0].text == text
    assert "RESPONSE_SEMANTIC_FILTER_DISABLED" in result.reason_codes


def test_disabled_filter_drops_unauthorized_state_proposals_without_rejecting_speech() -> None:
    context = _context()
    text = "Eu tenho uma opinião, mas não tô decidindo meu sexo."
    result = RealizationValidator.parse_and_validate(
        context,
        {
            "moves": [{"kind": "unknown_kind", "text": text}],
            "closing": "statement",
            "self_choices": [{"key": "sex", "value": "alterado"}],
        },
        enforce_dialogue_plan=False,
    )
    assert result.text == text
    assert result.frame.self_choices == []
    assert "UNAUTHORIZED_SELF_CHOICES_IGNORED" in result.reason_codes


def test_disabled_filter_still_rejects_invalid_json_structure() -> None:
    with pytest.raises(RealizationError) as exc:
        RealizationValidator.parse_and_validate(
            _context(), {"moves": [], "closing": "statement"},
            enforce_dialogue_plan=False,
        )
    assert exc.value.code == "REALIZATION_SCHEMA_INVALID"


@pytest.mark.asyncio
async def test_live_unfiltered_response_reaches_speech_without_fallback(
    tmp_path: Path, monkeypatch,
) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_RESPONSE_SEMANTIC_FILTER_ENABLED", "0")
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        provider = StructuredProvider(invalid=True)
        core.cognition.register_provider(provider)
        request = CognitiveRequest(
            text="Amora, o que você acha desse jogo?",
            speaker_id="PERSON_BK",
            target_id="ENTITY_AMORA",
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
        )
        plan = await core.cognition.respond(request, fallback_text="Hm.")
        assert plan.provider == provider.descriptor.name
        assert plan.fallback_used is False
        assert plan.display_text == "Como posso te ajudar hoje?"
        assert plan.speech_text == "Como posso te ajudar hoje?"
        assert plan.metadata["realization_validated"] is True
        assert plan.metadata["dialogue_plan_semantically_enforced"] is False
        assert core.cognition.state()["response_semantic_filter_enabled"] is False
        assert "RESPONSE_SEMANTIC_FILTER_DISABLED" in plan.reason_codes
        assert [segment.speech_text for segment in core.cognition.store.list_segments(request.turn_id)] == [
            "Como posso te ajudar hoje?"
        ]
    finally:
        core.close()
