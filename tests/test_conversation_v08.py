from datetime import datetime, timedelta, timezone

import pytest

from amora.app import AmoraCore
from amora.autonomy import DecisionContext, DriveType
from amora.events import PrivacyScope
from amora.time import SimulatedClock
from amora.conversation import SpeechAck, TurnSignal, TurnState, UtteranceStatus


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 9, 0, 30, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_turn_manager_respects_floor_and_backchannel(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        wait = core.conversation.decide_turn(TurnSignal(
            speaker_id="PERSON_BK",
            addressee_id="PERSON_OTHER",
            conversation_floor="PERSON_BK",
            vad_active=True,
            pause_ms=50,
            active_conversation=True,
            dedupe_key="floor-other",
        ))
        assert wait.state == TurnState.WAIT
        assert "CONVERSATION_FLOOR_OTHER" in wait.reason_codes

        backchannel, utterance = await core.conversation.enqueue_backchannel(TurnSignal(
            speaker_id="PERSON_BK",
            addressee_id="ENTITY_AMORA",
            conversation_floor="PERSON_BK",
            vad_active=True,
            pause_ms=260,
            partial_text="entendeu",
            active_conversation=True,
            dedupe_key="micro-pause",
        ), dedupe_key="micro-pause")
        assert backchannel.state == TurnState.BACKCHANNEL
        assert utterance is not None
        assert utterance.text == "Uhum."
        assert utterance.kind.value == "backchannel"
    finally:
        core.close()


@pytest.mark.asyncio
async def test_secret_and_auth_never_enter_speech_or_turn_history(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        before = core.conversation.store.turn_count()
        decision = core.conversation.decide_turn(TurnSignal(
            speaker_id="PERSON_BK",
            addressee_id="ENTITY_AMORA",
            partial_text="senha super secreta",
            privacy_scope=PrivacyScope.AUTH,
            dedupe_key="auth-turn",
        ))
        assert decision.state == TurnState.WAIT
        assert decision.signal.partial_text is None
        assert core.conversation.store.turn_count() == before

        item = await core.conversation.enqueue_text(
            "não fale isso",
            target_id="PERSON_BK",
            kind="semantic",
            priority=80,
            privacy_scope=PrivacyScope.SECRET,
            source_event_id=None,
            dedupe_key="secret-speech",
        )
        assert item is None
        assert core.conversation.store.pending_count() == 0
    finally:
        core.close()


@pytest.mark.asyncio
async def test_v07_semantic_action_becomes_one_v08_utterance(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        core.autonomy.adjust_drive(DriveType.SOCIAL, 0.55, reason="long silence", dedupe_key="v08-social-drive")
        cycle = await core.autonomy.cycle(
            DecisionContext(is_idle=True, available_entities={"PERSON_BK"}),
            dedupe_key="v08-social-cycle",
        )
        assert cycle.intention is not None
        result = await core.actions.execute_current(dedupe_key="v08-execute")
        assert result is not None
        assert result.plan.status.value == "completed"

        utterances = core.conversation.store.list_utterances()
        assert len(utterances) == 1
        assert utterances[0].text == "Papis?"
        assert utterances[0].target_id == "PERSON_BK"
        assert utterances[0].status == UtteranceStatus.QUEUED

        replay = await core.actions.execute_plan(result.plan.id, dedupe_key="different-key")
        assert replay.deduplicated is True
        assert len(core.conversation.store.list_utterances()) == 1
    finally:
        core.close()


@pytest.mark.asyncio
async def test_direct_completed_utterance_gets_local_reply_but_third_party_does_not(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        decision, reply = await core.conversation.observe_completed_utterance(
            "Oi Amora?",
            speaker_id="PERSON_BK",
            addressee_id="ENTITY_AMORA",
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
        )
        assert decision.state == TurnState.TAKE_TURN
        assert reply is not None
        assert "papis" in reply.text.lower()

        other_decision, other_reply = await core.conversation.observe_completed_utterance(
            "João, olha isso",
            speaker_id="PERSON_BK",
            addressee_id="PERSON_OTHER",
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
        )
        assert other_decision.state != TurnState.TAKE_TURN
        assert other_reply is None
    finally:
        core.close()


@pytest.mark.asyncio
async def test_claim_lease_requeues_after_crash_and_ack_is_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        item = await core.conversation.enqueue_text(
            "Papis?",
            target_id="PERSON_BK",
            kind="semantic",
            priority=70,
            privacy_scope=PrivacyScope.PERSONAL,
            source_event_id=None,
            dedupe_key="lease-test",
        )
        assert item is not None
        first = core.conversation.claim_next()
        assert first is not None
        assert first.status == UtteranceStatus.CLAIMED
        assert core.conversation.claim_next() is None

        clock.advance(timedelta(seconds=31))
        recovered = core.conversation.claim_next()
        assert recovered is not None
        assert recovered.id == first.id

        spoken = await core.conversation.acknowledge(recovered.id, SpeechAck(spoken=True))
        assert spoken is not None and spoken.status == UtteranceStatus.SPOKEN
        again = await core.conversation.acknowledge(recovered.id, SpeechAck(spoken=True))
        assert again is not None and again.status == UtteranceStatus.SPOKEN
        assert core.conversation.store.pending_count() == 0
        assert core.conversation.store.spoken_count() == 1
    finally:
        core.close()
