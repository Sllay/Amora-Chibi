from datetime import datetime, timedelta, timezone

import pytest

from amora.app import AmoraCore
from amora.emotion import AppraisalInput, EmotionType, SocialBidType
from amora.events import AmoraEvent, PrivacyScope
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 8, 22, 0, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_directed_anger_preserves_target_causality_and_spillover(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        result = await core.emotion.process(
            AppraisalInput(
                description="BK ignorou Amora de propósito",
                outcome_valence=-0.9,
                target_id="PERSON_BK",
                agent_id="PERSON_BK",
                goal_relevance=0.9,
                novelty=0.4,
                expectedness=0.5,
                agency=0.95,
                control=0.6,
                intentionality=0.95,
                norm_violation=0.85,
                relationship_relevance=0.95,
            )
        )
        assert result.event is not None
        assert result.event.emotion == EmotionType.ANGER
        assert result.event.target_id == "PERSON_BK"
        assert result.event.blame > 0.7
        assert result.target_affect is not None
        assert result.target_affect.anger > 0.5
        assert result.mood.valence < 0
        assert result.mood.arousal > 0
        assert result.social_bids[0].bid_type == SocialBidType.SET_BOUNDARY

        chatgpt = core.emotion.affect_for("ENTITY_CHATGPT")
        assert chatgpt.anger == pytest.approx(0.0)
        assert chatgpt.irritation == pytest.approx(0.0)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_affect_and_mood_decay_through_timeservice(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        result = await core.emotion.process(
            AppraisalInput(
                description="BK irritou Amora",
                outcome_valence=-0.7,
                target_id="PERSON_BK",
                agent_id="PERSON_BK",
                goal_relevance=0.7,
                agency=0.8,
                control=0.7,
                intentionality=0.7,
                norm_violation=0.5,
                relationship_relevance=0.8,
            )
        )
        assert result.target_affect is not None
        anger_before = result.target_affect.anger
        valence_before = abs(result.mood.valence)
        clock.advance(timedelta(hours=3))
        anger_after = core.emotion.affect_for("PERSON_BK").anger
        valence_after = abs(core.emotion.current_mood().valence)
        assert 0 <= anger_after < anger_before
        assert 0 <= valence_after < valence_before
    finally:
        core.close()


@pytest.mark.asyncio
async def test_gratitude_updates_only_target_and_creates_candidate_bid(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        result = await core.emotion.process(
            AppraisalInput(
                description="ChatGPT ajudou Amora",
                outcome_valence=0.9,
                target_id="ENTITY_CHATGPT",
                agent_id="ENTITY_CHATGPT",
                goal_relevance=0.8,
                novelty=0.5,
                agency=0.9,
                control=0.6,
                intentionality=0.9,
                relationship_relevance=0.9,
            )
        )
        assert result.event is not None
        assert result.event.emotion == EmotionType.GRATITUDE
        assert result.target_affect is not None
        assert result.target_affect.gratitude > 0.4
        assert result.target_affect.affection > 0
        assert result.target_affect.trust > 0.5
        assert result.social_bids[0].bid_type == SocialBidType.EXPRESS_GRATITUDE
        assert result.social_bids[0].metadata["requires_v0_6_decision"] is True
        assert core.emotion.affect_for("PERSON_BK").gratitude == pytest.approx(0.0)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_dedupe_does_not_duplicate_emotional_impact(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        item = AppraisalInput(
            description="BK fez uma piada irritante",
            outcome_valence=-0.55,
            target_id="PERSON_BK",
            agent_id="PERSON_BK",
            agency=0.8,
            control=0.7,
            intentionality=0.8,
            norm_violation=0.5,
            relationship_relevance=0.8,
            dedupe_key="same-real-world-event",
        )
        first = await core.emotion.process(item)
        assert first.target_affect is not None
        first_anger = first.target_affect.anger
        first_mood = first.mood.model_dump()
        second = await core.emotion.process(item)
        assert second.deduplicated is True
        assert core.emotion.store.event_count() == 1
        assert core.emotion.affect_for("PERSON_BK").anger == pytest.approx(first_anger)
        assert core.emotion.current_mood().model_dump(exclude={"updated_at"}) == {
            key: value for key, value in first_mood.items() if key != "updated_at"
        }
        assert second.social_bids == []
    finally:
        core.close()


@pytest.mark.asyncio
async def test_auth_never_enters_emotion_engine(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        with pytest.raises(ValueError, match="AUTH"):
            await core.emotion.process(
                AppraisalInput(
                    description="senha secreta",
                    outcome_valence=-1.0,
                    privacy_scope=PrivacyScope.AUTH,
                )
            )
        assert core.emotion.store.event_count() == 0
    finally:
        core.close()


@pytest.mark.asyncio
async def test_low_signal_appraisal_can_produce_no_emotion_event(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        result = await core.emotion.process(
            AppraisalInput(
                description="ruído irrelevante",
                outcome_valence=0.0,
                goal_relevance=0.0,
                novelty=0.0,
                expectedness=1.0,
                agency=0.0,
                control=1.0,
                intentionality=0.0,
                norm_violation=0.0,
                relationship_relevance=0.0,
            )
        )
        assert result.appraisal.emotion is None
        assert result.event is None
        assert core.emotion.store.event_count() == 0
    finally:
        core.close()


@pytest.mark.asyncio
async def test_social_bids_expire_instead_of_becoming_permanent_desires(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        result = await core.emotion.process(
            AppraisalInput(
                description="BK cruzou um limite",
                outcome_valence=-0.9,
                target_id="PERSON_BK",
                agent_id="PERSON_BK",
                agency=1.0,
                control=0.7,
                intentionality=1.0,
                norm_violation=1.0,
                relationship_relevance=0.9,
            )
        )
        assert result.social_bids
        assert core.emotion.pending_bids()
        clock.advance(timedelta(hours=2))
        assert core.emotion.pending_bids() == []
    finally:
        core.close()


@pytest.mark.asyncio
async def test_event_appraisal_preserves_cause_and_is_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        cause = AmoraEvent(event_type="social.boundary_crossed", source="pytest", actor="PERSON_BK")
        await core.events.publish(cause)
        result = await core.emotion.process_event(
            cause,
            outcome_valence=-0.8,
            goal_relevance=0.8,
            novelty=0.5,
            expectedness=0.4,
            agency=0.9,
            control=0.7,
            intentionality=0.9,
            norm_violation=0.9,
            relationship_relevance=0.9,
        )
        assert result.event is not None
        assert result.event.cause_event_id == cause.id
        duplicate = await core.emotion.process_event(
            cause,
            outcome_valence=-0.8,
            goal_relevance=0.8,
            novelty=0.5,
            expectedness=0.4,
            agency=0.9,
            control=0.7,
            intentionality=0.9,
            norm_violation=0.9,
            relationship_relevance=0.9,
        )
        assert duplicate.deduplicated is True
        assert core.emotion.store.event_count() == 1
    finally:
        core.close()
