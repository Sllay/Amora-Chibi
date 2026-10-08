from datetime import datetime, timedelta, timezone

import pytest

from amora.app import AmoraCore
from amora.autonomy import DecisionContext, DecisionOutcome, DesireKind, DriveType
from amora.emotion import AppraisalInput
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 8, 22, 0, tzinfo=timezone.utc))


@pytest.mark.asyncio
async def test_quiet_baseline_can_choose_silence_as_explicit_decision(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        result = await core.autonomy.cycle(DecisionContext(is_idle=True), dedupe_key="quiet-cycle")
        assert result.decision.outcome == DecisionOutcome.SILENCE
        assert result.intention is None
        assert "SILENCE_SELECTED" in result.decision.reason_codes
        assert core.autonomy.store.decision_count() == 1
    finally:
        core.close()


def test_drives_grow_with_timeservice_and_event_adjustments_are_idempotent(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        initial = {item.drive: item.need for item in core.autonomy.current_drives()}
        clock.advance(timedelta(hours=4))
        grown = {item.drive: item.need for item in core.autonomy.current_drives()}
        assert grown[DriveType.SOCIAL] > initial[DriveType.SOCIAL]
        updated, duplicate = core.autonomy.adjust_drive(
            DriveType.SOCIAL,
            -0.20,
            reason="social interaction satisfied need",
            dedupe_key="social-satisfaction-1",
        )
        assert duplicate is False
        replay, duplicate = core.autonomy.adjust_drive(
            DriveType.SOCIAL,
            -0.20,
            reason="social interaction satisfied need",
            dedupe_key="social-satisfaction-1",
        )
        assert duplicate is True
        assert replay.need == pytest.approx(updated.need)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_strong_social_bid_becomes_bdi_intention_without_executing_action(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        emotion = await core.emotion.process(
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
                dedupe_key="v06-source-emotion",
            )
        )
        assert emotion.social_bids
        result = await core.autonomy.cycle(
            DecisionContext(is_idle=True, available_entities={"PERSON_BK"}),
            dedupe_key="v06-social-bid-cycle",
        )
        assert result.decision.outcome == DecisionOutcome.SELECTED
        assert result.intention is not None
        assert result.intention.kind == DesireKind.SOCIAL_BID
        assert result.intention.target_id == "PERSON_BK"
        assert result.intention.metadata["execution_deferred_to_v0_7"] is True
        assert core.emotion.pending_bids(), "V0.6 must not consume an unexecuted bid"
    finally:
        core.close()


@pytest.mark.asyncio
async def test_hysteresis_keeps_current_intention_instead_of_thrashing(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    clock = _clock()
    core = AmoraCore.bootstrap(clock=clock)
    try:
        core.autonomy.adjust_drive(DriveType.SOCIAL, 0.55, reason="long silence")
        first = await core.autonomy.cycle(DecisionContext(is_idle=True), dedupe_key="first-intention")
        assert first.intention is not None
        assert first.intention.kind == DesireKind.INITIATE_CONTACT

        second = await core.autonomy.cycle(DecisionContext(is_idle=True), dedupe_key="second-intention")
        assert second.decision.outcome == DecisionOutcome.KEPT_CURRENT
        assert second.intention is not None
        assert second.intention.id == first.intention.id
        assert "HYSTERESIS_KEEP" in second.decision.reason_codes
    finally:
        core.close()


@pytest.mark.asyncio
async def test_context_can_defer_current_intention_without_erasing_it(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        core.autonomy.adjust_drive(DriveType.SOCIAL, 0.55, reason="social need")
        first = await core.autonomy.cycle(DecisionContext(is_idle=True))
        assert first.intention is not None
        deferred = await core.autonomy.cycle(DecisionContext(is_idle=False, can_interrupt=False))
        assert deferred.decision.outcome == DecisionOutcome.DEFERRED
        assert deferred.intention is not None
        assert deferred.intention.id == first.intention.id
        assert core.autonomy.store.current_intention().id == first.intention.id
    finally:
        core.close()


@pytest.mark.asyncio
async def test_decision_dedupe_survives_replay_without_new_intention(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        core.autonomy.adjust_drive(DriveType.SOCIAL, 0.55, reason="social need")
        first = await core.autonomy.cycle(DecisionContext(is_idle=True), dedupe_key="same-cycle")
        count = core.autonomy.store.decision_count()
        replay = await core.autonomy.cycle(DecisionContext(is_idle=True), dedupe_key="same-cycle")
        assert replay.deduplicated is True
        assert replay.decision.id == first.decision.id
        assert replay.intention is not None and first.intention is not None
        assert replay.intention.id == first.intention.id
        assert core.autonomy.store.decision_count() == count
    finally:
        core.close()
