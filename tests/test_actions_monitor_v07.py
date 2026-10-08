import asyncio
from datetime import datetime, timezone

import pytest

from amora.actions import ActionPlan, ExecutionMonitor, PlannerMode, PluginHealth, PluginManifest, ToolSpec
from amora.actions.models import PlanStep, RetryPolicy
from amora.app import AmoraCore
from amora.autonomy import DecisionContext, DriveType
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 9, 0, 15, tzinfo=timezone.utc))


async def _intention(core: AmoraCore):
    core.autonomy.adjust_drive(DriveType.SOCIAL, 0.55, reason="monitor test", dedupe_key="monitor-drive")
    cycle = await core.autonomy.cycle(
        DecisionContext(is_idle=True, available_entities={"PERSON_BK"}),
        dedupe_key="monitor-cycle",
    )
    assert cycle.intention is not None
    return cycle.intention


class _FlakyPlugin:
    def __init__(self) -> None:
        self.called = 0
        self._manifest = PluginManifest(
            plugin_id="test.flaky",
            name="Flaky",
            version="1",
            tools=[ToolSpec(name="run", output_schema={"type": "object", "required": ["ok"]})],
            health=PluginHealth.HEALTHY,
        )

    @property
    def manifest(self):
        return self._manifest

    async def execute(self, tool_name, arguments, call):
        self.called += 1
        if self.called == 1:
            raise RuntimeError("first attempt fails")
        return {"ok": True}

    async def verify(self, tool_name, arguments, output, call):
        return output.get("ok") is True


@pytest.mark.asyncio
async def test_retry_policy_recovers_from_isolated_plugin_failure(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        intention = await _intention(core)
        plugin = _FlakyPlugin()
        core.actions.registry.register(plugin)
        now = core.clock.now()
        plan = ActionPlan(
            intention_id=intention.id,
            intention_kind=intention.kind.value,
            target_id=intention.target_id,
            planner_mode=PlannerMode.RULE,
            steps=[
                PlanStep(
                    order=0,
                    plugin_id="test.flaky",
                    tool_name="run",
                    expected_output={"ok": True},
                    retry_policy=RetryPolicy(max_attempts=2),
                )
            ],
            created_at=now,
            updated_at=now,
        )
        assert core.actions.store.put_plan(plan)
        result = await core.actions.execute_plan(plan.id, dedupe_key="flaky-run")
        assert result.plan.status.value == "completed"
        assert [item.status.value for item in result.executions] == ["failed", "succeeded"]
        assert result.executions[0].monitor_outcome.value == "retry"
        assert result.executions[1].monitor_outcome.value == "verified"
        assert plugin.called == 2
    finally:
        core.close()


class _SlowVerifierPlugin:
    def __init__(self) -> None:
        self._manifest = PluginManifest(
            plugin_id="test.slowverify",
            name="Slow verifier",
            version="1",
            tools=[ToolSpec(name="run", output_schema={"type": "object", "required": ["ok"]})],
            health=PluginHealth.HEALTHY,
        )

    @property
    def manifest(self):
        return self._manifest

    async def execute(self, tool_name, arguments, call):
        return {"ok": True}

    async def verify(self, tool_name, arguments, output, call):
        await asyncio.sleep(0.05)
        return True


@pytest.mark.asyncio
async def test_verification_timeout_fails_closed_and_requests_replan(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        intention = await _intention(core)
        core.actions.registry.register(_SlowVerifierPlugin())
        core.actions.monitor = ExecutionMonitor(verify_timeout_seconds=0.001)
        now = core.clock.now()
        plan = ActionPlan(
            intention_id=intention.id,
            intention_kind=intention.kind.value,
            target_id=intention.target_id,
            planner_mode=PlannerMode.RULE,
            steps=[
                PlanStep(
                    order=0,
                    plugin_id="test.slowverify",
                    tool_name="run",
                    expected_output={"ok": True},
                    retry_policy=RetryPolicy(max_attempts=1),
                )
            ],
            created_at=now,
            updated_at=now,
        )
        assert core.actions.store.put_plan(plan)
        result = await core.actions.execute_plan(plan.id, dedupe_key="slow-verify-run")
        assert result.plan.status.value == "failed"
        assert result.monitor_outcome.value == "replan"
        assert result.executions[0].error_code == "EFFECT_VERIFICATION_TIMEOUT"
        assert result.executions[0].verified is False
        assert core.autonomy.store.current_intention() is not None
    finally:
        core.close()
