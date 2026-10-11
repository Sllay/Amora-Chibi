from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveJournalEntry, CognitiveRequest, ConversationMessage, JournalStage, MessageSource, MessageStatus, SpeechNormalizer, SpeechSegmenter
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass
from amora.time import SimulatedClock
from amora.world import SelfLayer


class FakeStructuredProvider:
    def __init__(self, name: str = "fake-structured") -> None:
        self.descriptor = ProviderDescriptor(
            name=name,
            model=f"{name}-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=1.0,
            latency_score=1.0,
            cost_score=0.0,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL, PrivacyScope.PRIVATE}),
            supports_streaming=False,
            supports_structured_output=True,
            context_window_tokens=100_000,
            priority=20,
        )

    async def realize(self, context):
        assert context.speaker_id == "PERSON_BK"
        assert context.self_model["core_identity"]["entity_id"] == "ENTITY_AMORA"
        allowed = list(context.dialogue_plan["response_moves"])
        return {
            "moves": [
                {
                    "kind": allowed[0],
                    "text": "Claro, papis 💜. Eu lembro disso — e queria comentar uma coisa.",
                }
            ],
            "closing": "statement",
            "_usage": {"input_tokens": 321, "output_tokens": 19, "latency_ms": 42.0, "status_code": 200},
        }


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 10, 1, 45, tzinfo=timezone.utc))


def test_speech_normalizer_keeps_display_but_never_reads_formatting() -> None:
    rendered = SpeechNormalizer().normalize('**“Oi, papis 💜”** (risos) kkkkk — tudo bem?')
    assert rendered.display_text.startswith("**")
    assert rendered.speech_text == "Oi, papis, tudo bem?"
    assert "soft_laugh" in rendered.nonverbal_cues
    assert "coração" not in rendered.speech_text
    assert "aspas" not in rendered.speech_text


def test_segmenter_releases_stable_clause_before_full_response() -> None:
    segmenter = SpeechSegmenter()
    assert segmenter.feed("Papis, eu tava pensando") == []
    assert segmenter.feed(" nisso. E tem mais uma coisa") == ["Papis, eu tava pensando nisso."]
    assert segmenter.flush() == ["E tem mais uma coisa"]


@pytest.mark.asyncio
async def test_validated_response_persists_segments_and_actual_spoken_text(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); core = AmoraCore.bootstrap(clock=_clock())
    try:
        core.cognition.register_provider(FakeStructuredProvider()); queued = []
        async def sink(segment): queued.append(segment)
        request = CognitiveRequest(text="Amora, lembra disso?", speaker_id="PERSON_BK", target_id="ENTITY_AMORA", privacy_scope=PrivacyScope.PERSONAL, remote_processing_allowed=True)
        plan = await core.cognition.respond(request, on_segment=sink)
        assert plan.provider == "fake-structured" and plan.fallback_used is False and len(queued) == 2
        assert queued[0].speech_text == "Claro, papis."
        assert queued[1].speech_text == "Eu lembro disso, e queria comentar uma coisa."
        assert plan.input_tokens == 321 and plan.output_tokens == 19
        assert plan.metadata["realization_validated"] is True
        first = queued[0]
        core.cognition.mark_segment_speaking(first.id, turn_id=request.turn_id)
        core.cognition.acknowledge_segment(first.id, spoken=True, turn_id=request.turn_id)
        messages = core.cognition.store.list_messages(turn_id=request.turn_id); amora_messages = [item for item in messages if item.speaker_id == "ENTITY_AMORA"]
        assert amora_messages[0].status == MessageStatus.SPOKEN and amora_messages[0].spoken_text == "Claro, papis."
        assert amora_messages[1].spoken_text is None
        assert core.cognition.interrupt_turn(request.turn_id) == 1
        segments = core.cognition.store.list_segments(request.turn_id)
        assert segments[0].status == MessageStatus.SPOKEN and segments[1].status == MessageStatus.INTERRUPTED
        assert any(item.stage == JournalStage.BARGE_IN for item in core.cognition.store.journal_for_turn(request.turn_id))
    finally: core.close()


def test_auth_content_is_redacted_from_history_and_journal(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); core = AmoraCore.bootstrap(clock=_clock())
    try:
        turn_id = CognitiveRequest(text="auth fixture", speaker_id="PERSON_BK").turn_id
        marker = "AUTH_PAYLOAD_FIXTURE"
        core.cognition.store.put_message(ConversationMessage(turn_id=turn_id, speaker_id="PERSON_BK", addressee_id="ENTITY_AMORA", source=MessageSource.TEXT, raw_text=marker, normalized_text=marker, spoken_text=marker, privacy_scope=PrivacyScope.AUTH, remote_processing_allowed=True, started_at=core.clock.now(), status=MessageStatus.RECEIVED))
        core.cognition.store.put_journal(CognitiveJournalEntry(turn_id=turn_id, stage=JournalStage.INPUT_OBSERVED, timestamp=core.clock.now(), summary=marker, payload={"fixture": marker}, privacy_scope=PrivacyScope.AUTH))
        message = core.cognition.store.list_messages(turn_id=turn_id)[0]; journal = core.cognition.store.journal_for_turn(turn_id)[0]
        assert message.raw_text is None and message.normalized_text is None and message.spoken_text is None
        assert message.remote_processing_allowed is False and message.metadata["content_redacted"] is True
        assert journal.summary == "REDACTED" and journal.payload == {} and journal.metadata["content_redacted"] is True
    finally: core.close()


@pytest.mark.asyncio
async def test_history_and_journal_survive_restart_and_provider_identity_does_not(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); clock = _clock(); first = AmoraCore.bootstrap(clock=clock)
    try:
        first.cognition.register_provider(FakeStructuredProvider("provider-a")); request = CognitiveRequest(text="Amora, lembra daquele assunto?", speaker_id="PERSON_BK", target_id="ENTITY_AMORA", privacy_scope=PrivacyScope.PERSONAL, remote_processing_allowed=True)
        plan = await first.cognition.respond(request); assert plan.provider == "provider-a"; turn_id = request.turn_id; assert first.cognition.store.message_count() >= 3
    finally: first.close()
    second = AmoraCore.bootstrap(clock=clock)
    try:
        assert second.cognition.store.list_messages(turn_id=turn_id); assert second.cognition.store.journal_for_turn(turn_id)
        assert second.self_model.get(SelfLayer.CORE_IDENTITY, "entity_id").value == "ENTITY_AMORA"
        second.cognition.register_provider(FakeStructuredProvider("provider-b")); next_request = CognitiveRequest(text="Amora, continua sendo você?", speaker_id="PERSON_BK", target_id="ENTITY_AMORA", privacy_scope=PrivacyScope.PERSONAL, remote_processing_allowed=True)
        plan = await second.cognition.respond(next_request); assert plan.provider == "provider-b"
        assert second.self_model.get(SelfLayer.CORE_IDENTITY, "entity_id").value == "ENTITY_AMORA"
    finally: second.close()


@pytest.mark.asyncio
async def test_remote_processing_disabled_forces_local_fallback(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora")); core = AmoraCore.bootstrap(clock=_clock())
    try:
        core.cognition.register_provider(FakeStructuredProvider()); request = CognitiveRequest(text="Não mande isso pra fora", speaker_id="PERSON_BK", privacy_scope=PrivacyScope.PRIVATE, remote_processing_allowed=False)
        plan = await core.cognition.respond(request, fallback_text="Tá bom, papis.")
        assert plan.fallback_used is True and plan.provider is None and "REMOTE_PROCESSING_NOT_ALLOWED" in plan.reason_codes
        assert any("REMOTE_PROCESSING_NOT_ALLOWED" in item.reason_codes for item in core.cognition.store.journal_for_turn(request.turn_id))
    finally: core.close()
