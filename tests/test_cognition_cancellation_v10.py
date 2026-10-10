from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest, JournalStage, MessageStatus, ProviderDelta
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.time import SimulatedClock


class BlockingStreamingProvider:
    def __init__(self) -> None:
        self.closed = asyncio.Event()
        self.descriptor = ProviderDescriptor(
            name="blocking-stream",
            model="blocking-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=1.0,
            latency_score=1.0,
            cost_score=0.0,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
            supports_streaming=True,
            context_window_tokens=100_000,
            priority=50,
        )

    async def stream(self, context):
        try:
            yield ProviderDelta(text="Primeira frase. Segunda parte ainda está sendo gerada")
            await asyncio.Event().wait()
            yield ProviderDelta(text=" e nunca deve virar fala depois do barge-in.")
        finally:
            self.closed.set()


class FiniteStreamingProvider:
    def __init__(self) -> None:
        self.descriptor = ProviderDescriptor(
            name="finite-stream",
            model="finite-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=1.0,
            latency_score=1.0,
            cost_score=0.0,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
            supports_streaming=True,
            context_window_tokens=100_000,
            priority=40,
        )

    async def stream(self, context):
        yield ProviderDelta(text="Claro, papis. E eu termino esta frase também.")
        yield ProviderDelta(input_tokens=20, output_tokens=12, latency_ms=5.0, status_code=200)
        yield ProviderDelta(done=True, finish_reason="stop")


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 10, 2, 10, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_barge_in_cancels_provider_stream_and_blocks_future_segments(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        provider = BlockingStreamingProvider()
        core.cognition.register_provider(provider)
        first_segment = asyncio.Event()

        async def sink(segment) -> None:
            first_segment.set()

        request = CognitiveRequest(
            text="Amora, fala comigo",
            speaker_id="PERSON_BK",
            target_id="ENTITY_AMORA",
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
        )
        response_task = asyncio.create_task(core.cognition.respond(request, on_segment=sink))
        await asyncio.wait_for(first_segment.wait(), timeout=2.0)

        assert core.cognition.interrupt_turn(request.turn_id, reason="barge_in") == 1
        plan = await asyncio.wait_for(response_task, timeout=2.0)
        await asyncio.wait_for(provider.closed.wait(), timeout=2.0)

        assert plan.metadata["cancelled"] is True
        assert "PROVIDER_STREAM_CANCELLED" in plan.reason_codes
        segments = core.cognition.store.list_segments(request.turn_id)
        assert len(segments) == 1
        assert segments[0].status == MessageStatus.INTERRUPTED
        stages = [item.stage for item in core.cognition.store.journal_for_turn(request.turn_id)]
        assert JournalStage.BARGE_IN in stages
        assert stages.count(JournalStage.TURN_FINISHED) == 1
    finally:
        core.close()


@pytest.mark.asyncio
async def test_turn_finishes_only_after_delivery_settles(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        core.cognition.register_provider(FiniteStreamingProvider())
        request = CognitiveRequest(
            text="Oi Amora",
            speaker_id="PERSON_BK",
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
        )
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
