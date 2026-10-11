from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.time import SimulatedClock


class BoundaryProvider:
    def __init__(self) -> None:
        self.seen_plan: dict[str, object] | None = None
        self.descriptor = ProviderDescriptor(
            name="boundary-fixture",
            model="boundary-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=1.0,
            latency_score=1.0,
            cost_score=0.0,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
            supports_streaming=False,
            supports_structured_output=True,
            context_window_tokens=32_000,
            priority=100,
        )

    async def realize(self, context):
        self.seen_plan = dict(context.dialogue_plan)
        assert context.dialogue_plan["act"] == "set_boundary"
        assert context.dialogue_plan["stance"] == "firm"
        assert context.dialogue_plan["relational_attitude"] == "guarded"
        return {
            "moves": [
                {"kind": "name_impact", "text": "Isso me incomodou."},
                {"kind": "set_boundary", "text": "Não fala comigo desse jeito."},
            ],
            "closing": "statement",
            "_usage": {"input_tokens": 18, "output_tokens": 12, "status_code": 200},
        }


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 11, 20, 30, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_direct_hostility_updates_affect_before_same_turn_dialogue_plan(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        provider = BoundaryProvider()
        core.cognition.register_provider(provider)
        before = core.emotion.affect_for("PERSON_BK")

        _, utterance = await core.conversation.observe_completed_utterance(
            "Amora, cala a boca, sua inútil.",
            speaker_id="PERSON_BK",
            addressee_id="ENTITY_AMORA",
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
        )

        after = core.emotion.affect_for("PERSON_BK")
        assert utterance is not None
        assert provider.seen_plan is not None
        assert provider.seen_plan["act"] == "set_boundary"
        assert after.anger > before.anger or after.irritation > before.irritation
        assert utterance.text == "Isso me incomodou."
    finally:
        core.close()


@pytest.mark.asyncio
async def test_test_turn_is_excluded_as_one_causal_unit_from_future_history(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        marker = "TESTE_TECNICO_NAO_APRENDER_9271"
        decision, utterance = await core.conversation.observe_completed_utterance(
            f"Amora, {marker}",
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
        assert utterance is not None
        assert decision.state.value == "take_turn"

        test_turn_id = str(utterance.metadata["cognitive_turn_id"])
        messages = core.cognition.store.list_messages(turn_id=test_turn_id)
        assert messages
        assert any(item.metadata.get("test_mode") is True for item in messages)

        next_request = CognitiveRequest(
            text="Amora, agora é uma conversa normal.",
            speaker_id="PERSON_BK",
            target_id="ENTITY_AMORA",
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=False,
        )
        context = core.cognition.compiler.compile(next_request)
        history_text = " ".join(str(item.get("text") or "") for item in context.recent_messages)
        assert marker not in history_text
        assert "TEST_TURNS_EXCLUDED_FROM_RECENT" in context.reason_codes
    finally:
        core.close()
