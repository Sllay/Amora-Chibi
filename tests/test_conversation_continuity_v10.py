from datetime import datetime, timedelta, timezone

import pytest

from amora.app import AmoraCore
from amora.events import PrivacyScope
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 10, 4, 20, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_followup_without_vocative_inherits_amora_for_short_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        first_decision, first_reply = await core.conversation.observe_completed_utterance(
            "Amora, tu ta me ouvindo?",
            speaker_id="PERSON_BK",
            addressee_id="ENTITY_AMORA",
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
        )
        assert first_reply is not None
        assert first_decision.signal.addressee_id == "ENTITY_AMORA"

        clock.advance(timedelta(seconds=8))
        follow_decision, follow_reply = await core.conversation.observe_completed_utterance(
            "agora me responde uma coisinha rapidinho",
            speaker_id="PERSON_BK",
            addressee_id=None,
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
        )
        assert follow_reply is not None
        assert follow_decision.signal.addressee_id == "ENTITY_AMORA"
    finally:
        core.close()


@pytest.mark.asyncio
async def test_inferred_continuity_does_not_renew_original_direct_window(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        _, first_reply = await core.conversation.observe_completed_utterance(
            "Amora, olha isso",
            speaker_id="PERSON_BK",
            addressee_id="ENTITY_AMORA",
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
        )
        assert first_reply is not None

        # Still inside the explicit t=0..25s window.
        clock.advance(timedelta(seconds=20))
        inferred_decision, inferred_reply = await core.conversation.observe_completed_utterance(
            "e isso aqui também",
            speaker_id="PERSON_BK",
            addressee_id=None,
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
        )
        assert inferred_reply is not None
        assert inferred_decision.signal.addressee_id == "ENTITY_AMORA"

        # If the inferred turn refreshed the deadline this would still target
        # Amora. The fixed window must instead expire at t=25s.
        clock.advance(timedelta(seconds=6))
        expired_decision, expired_reply = await core.conversation.observe_completed_utterance(
            "continua aí",
            speaker_id="PERSON_BK",
            addressee_id=None,
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
        )
        assert expired_reply is None
        assert expired_decision.signal.addressee_id is None
    finally:
        core.close()


@pytest.mark.asyncio
async def test_direct_continuity_expires_and_explicit_other_target_closes_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        _, first_reply = await core.conversation.observe_completed_utterance(
            "Amora, olha isso",
            speaker_id="PERSON_BK",
            addressee_id="ENTITY_AMORA",
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
        )
        assert first_reply is not None

        clock.advance(timedelta(seconds=26))
        expired_decision, expired_reply = await core.conversation.observe_completed_utterance(
            "ta comigo, meu anjo?",
            speaker_id="PERSON_BK",
            addressee_id=None,
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
        )
        assert expired_reply is None
        assert expired_decision.signal.addressee_id is None

        _, reopened_reply = await core.conversation.observe_completed_utterance(
            "Amora, mais uma coisa",
            speaker_id="PERSON_BK",
            addressee_id="ENTITY_AMORA",
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
        )
        assert reopened_reply is not None

        _, other_reply = await core.conversation.observe_completed_utterance(
            "Joao, olha isso",
            speaker_id="PERSON_BK",
            addressee_id="PERSON_JOAO",
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
        )
        assert other_reply is None

        follow_decision, follow_reply = await core.conversation.observe_completed_utterance(
            "e isso aqui tambem",
            speaker_id="PERSON_BK",
            addressee_id=None,
            source_event_id=None,
            privacy_scope=PrivacyScope.PERSONAL,
        )
        assert follow_reply is None
        assert follow_decision.signal.addressee_id is None
    finally:
        core.close()
