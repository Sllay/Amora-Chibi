from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.cognition import ProviderDelta
from amora.events import AmoraEvent, PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.time import SimulatedClock


class AutonomousProvider:
    def __init__(self) -> None:
        self.seen_autonomous_context = False
        self.descriptor = ProviderDescriptor(
            name="autonomy-provider",
            model="autonomy-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=1.0,
            latency_score=1.0,
            cost_score=0.0,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
            supports_streaming=True,
            context_window_tokens=100_000,
            priority=80,
        )

    async def stream(self, context):
        assert "AUTONOMOUS_INITIATION" in context.reason_codes
        assert "não é uma fala recebida" in context.user_text.lower()
        self.seen_autonomous_context = True
        yield ProviderDelta(text="Papis, pensei em você. Quer me mostrar o que tá fazendo?")
        yield ProviderDelta(done=True, input_tokens=35, output_tokens=15, latency_ms=7.0, status_code=200)


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 10, 2, 40, tzinfo=timezone.utc))


def _event(core: AmoraCore) -> AmoraEvent:
    return AmoraEvent(
        event_type="action.semantic_request",
        timestamp=core.clock.now(),
        source="test",
        actor="ENTITY_AMORA",
        target="PERSON_BK",
        confidence=1.0,
        importance=0.8,
        payload={"action_kind": "initiate_contact", "intention_id": "fixture-intention"},
        privacy_scope=PrivacyScope.PERSONAL,
    )


@pytest.mark.asyncio
async def test_autonomous_semantic_action_uses_canonical_cognition_when_opted_in(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_REMOTE_COGNITION_ALLOWED", "true")
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        provider = AutonomousProvider()
        core.cognition.register_provider(provider)
        item = await core.conversation.handle_semantic_event(_event(core))
        assert item is not None
        assert provider.seen_autonomous_context is True
        assert item.text == "Papis, pensei em você."
        assert item.metadata["autonomous"] is True
        assert item.metadata["cognitive_turn_id"]
    finally:
        core.close()


@pytest.mark.asyncio
async def test_autonomous_remote_cognition_is_separate_opt_in(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.delenv("AMORA_REMOTE_COGNITION_ALLOWED", raising=False)
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        provider = AutonomousProvider()
        core.cognition.register_provider(provider)
        item = await core.conversation.handle_semantic_event(_event(core))
        assert item is not None
        assert provider.seen_autonomous_context is False
        assert item.metadata.get("autonomous") is not True
    finally:
        core.close()
