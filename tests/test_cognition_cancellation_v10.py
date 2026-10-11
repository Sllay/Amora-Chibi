from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest, JournalStage
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.time import SimulatedClock


class BlockingStructuredProvider:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.closed = asyncio.Event()
        self.descriptor = ProviderDescriptor(
            name="blocking-structured",
            model="blocking-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=1.0,
            latency_score=1.0,
            cost_score=0.0,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
            supports_streaming=False,
            supports_structured_output=True,
            context_window_tokens=100_000,
            priority=50,
        )

    async def realize(self, context):
        self.started.set()
        try:
            await asyncio.Event().wait()
            raise AssertionError("unreachable")
        finally:
            self.closed.set()


class FiniteStructuredProvider:
    def __init__(self) -> None:
        self.descriptor = ProviderDescriptor(
            name="finite-structured",
            model="finite-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=1.0,
            latency_score=1.0,
            cost_score=0.0,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
            supports_streaming=False,
            supports_structured_output=True,
            context_window_tokens=100_000,
            priority=40,
        )

    async def realize(self, context):
        allowed = list(context.dialogue_plan["response_moves"])
        return {
            "moves": [
                {
                    "kind": allowed[0],
                    "text": "Claro, papis. Eu termino esta frase também.",
                }
            ],
            "closing": "statement",
            "_usage": {"input_tokens": 20, "output_tokens": 12, "latency_ms": 5.0, "status_code": 200},
        }


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 10, 2, 10, tzinfo=timezone.utc))


def _request() -> CognitiveRequest:
    return CognitiveRequest(
        text="Amora, me conta o que você acha desse jogo.",
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
    )


@pytest.mark.asyncio
async def test_barge_in_cancels_inflight_realization_before_any_speech(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        provider = BlockingStructuredProvider()
        core.cognition.register_provider(provider)
        request = _request()
        response_task = asyncio.create_task(core.cognition.respond(request))
        await asyncio.wait_for(provider.started.wait(), timeout=2.0)

        # The provider has not produced a valid RealizationFrame, therefore there
        # is intentionally no partial speech to mark interrupted.
        assert core.cognition.interrupt_turn(request.turn_id, reason="barge_in") == 0
        plan = await asyncio.wait_for(response_task, timeout=2.0)
        await asyncio.wait_for(provider.closed.wait(), timeout=2.0)

        assert plan.metadata["cancelled"] is True
        assert "PROVIDER_STREAM_CANCELLED" in plan.reason_codes
        assert core.cognition.store.list_segments(request.turn_id) == []
        stages = [item.stage for item in core.cognition.store.journal_for_turn(request.turn_id)]
        assert JournalStage.BARGE_IN in stages
        assert stages.count(JournalStage.TURN_FINISHED) == 1
    finally:
        core.close()


@pytest.mark.asyncio
async def test_turn_finishes_only_after_validated_delivery_settles(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        core.cognition.register_provider(FiniteStructuredProvider())
        request = _request()
        plan = await core.cognition.respond(request)
        journal = core.cognition.store.journal_for_turn(request.turn_id)
        assert not any(item.stage == JournalStage.TURN_FINISHED for item in journal)

        segments = core.cognition.store.list_segments(request.turn_id)
        for segment in segments:
            core.cognition.mark_segment_speaking(segment.id, turn_id=request.turn_id)
            core.cognition.acknowledge_segment(segment.id, spoken=True, turn_id=request.turn_id)

        journal = core.cognition.store.journal_for_turn(request.turn_id)
        assert sum(item.stage == JournalStage.TURN_FINISHED for item in journal) == 1
        assert plan.speech_segments
    finally:
        core.close()
