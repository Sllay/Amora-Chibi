from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.autonomy import DecisionContext, DriveType
from amora.time import Scheduler, SchedulerRuntime, SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 31, 12, 0, tzinfo=timezone.utc))


def _boost_social_drive(core: AmoraCore, *, key: str) -> None:
    core.autonomy.adjust_drive(
        DriveType.SOCIAL,
        0.55,
        reason="runtime wiring test",
        dedupe_key=key,
    )


@pytest.mark.asyncio
async def test_manual_autonomy_cycle_preserves_explicit_action_contract(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        _boost_social_drive(core, key="manual-drive")
        result = await core.autonomy.cycle(
            DecisionContext(is_idle=True, available_entities={"PERSON_BK"}),
            dedupe_key="manual-cycle",
        )

        assert result.decision.outcome.value == "selected"
        assert core.autonomy.store.current_intention() is not None
        assert core.actions.store.plan_count() == 0
        assert core.actions.store.run_count() == 0
    finally:
        core.close()


@pytest.mark.asyncio
async def test_runtime_marked_autonomy_decision_executes_through_guarded_actions(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        _boost_social_drive(core, key="runtime-drive")
        result = await core.autonomy.cycle(
            DecisionContext(
                is_idle=True,
                available_entities={"PERSON_BK"},
                metadata={"runtime_auto_execute": True},
            ),
            dedupe_key="runtime-cycle",
        )

        assert result.decision.outcome.value == "selected"
        assert core.actions.store.plan_count() == 1
        assert core.actions.store.run_count() == 1
        assert core.actions.store.execution_count() == 1
        assert core.autonomy.store.current_intention() is None
        assert core.conversation.store.pending_count() == 1
    finally:
        core.close()


@pytest.mark.asyncio
async def test_scheduler_runtime_defers_idle_job_until_idle() -> None:
    clock = _clock()
    scheduler = Scheduler(clock)
    idle = False
    calls: list[str] = []
    scheduler.schedule_idle("probe", 5.0, lambda: calls.append("ran"))
    runtime = SchedulerRuntime(scheduler, is_idle=lambda: idle, poll_interval_seconds=0.01)

    clock.advance(timedelta(seconds=5))
    assert await runtime.run_once() == []
    assert calls == []

    idle = True
    clock.advance(timedelta(seconds=1))
    assert await runtime.run_once() == ["probe"]
    assert calls == ["ran"]


def test_api_lifespan_arms_and_stops_autonomy_scheduler(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    app = create_api(core)
    try:
        with TestClient(app):
            jobs = {item["job_id"] for item in core.scheduler.snapshot()}
            assert "autonomy.idle" in jobs
            assert app.state.scheduler_runtime.running is True

        assert app.state.scheduler_runtime.running is False
        assert "autonomy.idle" not in {item["job_id"] for item in core.scheduler.snapshot()}
    finally:
        core.close()
