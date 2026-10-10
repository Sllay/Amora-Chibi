from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.cognition import MessageStatus, ProviderDelta
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.time import SimulatedClock


class SpeechProvider:
    def __init__(self) -> None:
        self.descriptor = ProviderDescriptor(
            name="speech-provider",
            model="speech-model",
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
        yield ProviderDelta(text="Primeira frase falada com carinho. Segunda frase fica na fila.")
        yield ProviderDelta(done=True, input_tokens=10, output_tokens=12, status_code=200)


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 10, 5, 30, tzinfo=timezone.utc))


async def _queue_cognitive_reply(core: AmoraCore):
    core.cognition.register_provider(SpeechProvider())
    _, item = await core.conversation.observe_completed_utterance(
        "Oi Amora",
        speaker_id="PERSON_BK",
        addressee_id="ENTITY_AMORA",
        source_event_id=None,
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
    )
    assert item is not None
    claimed = core.conversation.claim_next()
    assert claimed is not None
    return claimed


@pytest.mark.asyncio
async def test_local_tts_interruption_persists_known_spoken_prefix(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        claimed = await _queue_cognitive_reply(core)
        turn_id = str(claimed.metadata["cognitive_turn_id"])
        segment_id = str(claimed.metadata["cognitive_segment_id"])
        segment_before = next(item for item in core.cognition.store.list_segments(turn_id) if str(item.id) == segment_id)
        spoken_chars = 11

        _, cancelled = await core.conversation.cancel_utterance(
            claimed.id,
            reason="barge_in",
            spoken_chars=spoken_chars,
        )
        assert cancelled >= 1

        segment = next(item for item in core.cognition.store.list_segments(turn_id) if str(item.id) == segment_id)
        assert segment.status == MessageStatus.INTERRUPTED
        assert segment.metadata["spoken_progress_known"] is True
        assert segment.metadata["spoken_chars"] == spoken_chars

        messages = core.cognition.store.list_messages(turn_id=turn_id)
        spoken_message = next(item for item in messages if item.metadata.get("segment_id") == segment_id)
        assert spoken_message.status == MessageStatus.INTERRUPTED
        assert spoken_message.spoken_text == segment_before.speech_text[:spoken_chars]
    finally:
        core.close()


@pytest.mark.asyncio
async def test_remote_pcm_interruption_marks_spoken_prefix_unknown(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        claimed = await _queue_cognitive_reply(core)
        turn_id = str(claimed.metadata["cognitive_turn_id"])
        segment_id = str(claimed.metadata["cognitive_segment_id"])

        await core.conversation.cancel_utterance(
            claimed.id,
            reason="barge_in",
            spoken_chars=None,
        )

        segment = next(item for item in core.cognition.store.list_segments(turn_id) if str(item.id) == segment_id)
        assert segment.status == MessageStatus.INTERRUPTED
        assert segment.metadata["spoken_progress_known"] is False
        assert segment.metadata["spoken_chars"] is None
        messages = core.cognition.store.list_messages(turn_id=turn_id)
        spoken_message = next(item for item in messages if item.metadata.get("segment_id") == segment_id)
        assert spoken_message.spoken_text is None
    finally:
        core.close()
