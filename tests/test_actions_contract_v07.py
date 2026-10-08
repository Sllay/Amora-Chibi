from datetime import datetime, timezone
from uuid import uuid4

import pytest

from amora.actions import (
    ActionBus,
    ActionPlan,
    ExecutionContext,
    PlannerMode,
    PluginHealth,
    PluginManifest,
    PluginRegistry,
    RiskLevel,
    ToolCall,
    ToolExecutionStatus,
    ToolSpec,
)
from amora.actions.models import PlanStep, RetryPolicy
from amora.app import AmoraCore
from amora.autonomy import DecisionContext, DriveType
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 9, 0, 0, tzinfo=timezone.utc))


async def _social_intention(core: AmoraCore):
    core.autonomy.adjust_drive(DriveType.SOCIAL, 0.55, reason="contract test", dedupe_key="contract-drive")
    cycle = await core.autonomy.cycle(
        DecisionContext(is_idle=True, available_entities={"PERSON_BK"}),
        dedupe_key="contract-cycle",
    )
    assert cycle.intention is not None
    return cycle.intention


@pytest.mark.asyncio
async def test_builtin_input_schema_rejects_missing_arguments(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        call = ToolCall(
            plan_id=uuid4(),
            step_id=uuid4(),
            intention_id=uuid4(),
            plugin_id="core.actions",
            tool_name="emit_intention",
            arguments={},
            created_at=core.clock.now(),
        )
        result = await core.actions.bus.dispatch(call, core.actions.default_context())
        assert result.status == ToolExecutionStatus.DENIED
        assert result.error_code == "INPUT_SCHEMA_INVALID"
    finally:
        core.close()


class _MalformedOutputPlugin:
    def __init__(self) -> None:
        self._manifest = PluginManifest(
            plugin_id="test.malformed",
            name="Malformed Output",
            version="1",
            permissions={"test:malformed"},
            tools=[
                ToolSpec(
                    name="run",
                    permissions={"test:malformed"},
                    output_schema={"type": "object", "required": ["ok"]},
                )
            ],
            health=PluginHealth.HEALTHY,
        )

    @property
    def manifest(self):
        return self._manifest

    async def execute(self, tool_name, arguments, call):
        return {"wrong": True}

    async def verify(self, tool_name, arguments, output, call):
        return True


@pytest.mark.asyncio
async def test_output_schema_is_enforced_before_verification() -> None:
    clock = _clock()
    registry = PluginRegistry()
    registry.register(_MalformedOutputPlugin())
    bus = ActionBus(registry, clock)
    call = ToolCall(
        plan_id=uuid4(),
        step_id=uuid4(),
        intention_id=uuid4(),
        plugin_id="test.malformed",
        tool_name="run",
        created_at=clock.now(),
    )
    result = await bus.dispatch(call, ExecutionContext(granted_permissions={"test:malformed"}))
    assert result.status == ToolExecutionStatus.FAILED
    assert result.error_code == "OUTPUT_SCHEMA_INVALID"


class _CostlyPlugin:
    def __init__(self) -> None:
        self.called = 0
        self._manifest = PluginManifest(
            plugin_id="test.costly",
            name="Costly",
            version="1",
            permissions={"test:costly"},
            tools=[ToolSpec(name="run", permissions={"test:costly"}, cost=0.06)],
            health=PluginHealth.HEALTHY,
        )

    @property
    def manifest(self):
        return self._manifest

    async def execute(self, tool_name, arguments, call):
        self.called += 1
        return {"ok": True}

    async def verify(self, tool_name, arguments, output, call):
        return output.get("ok") is True


@pytest.mark.asyncio
async def test_whole_plan_budget_counts_retry_ceiling_before_execution(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        intention = await _social_intention(core)
        plugin = _CostlyPlugin()
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
                    plugin_id="test.costly",
                    tool_name="run",
                    retry_policy=RetryPolicy(max_attempts=2),
                )
            ],
            created_at=now,
            updated_at=now,
        )
        assert core.actions.store.put_plan(plan)
        result = await core.actions.execute_plan(plan.id, dedupe_key="cost-budget")
        assert result.plan.status.value == "aborted"
        assert "PLAN_COST_LIMIT_EXCEEDED" in result.plan.reason_codes
        assert result.executions == []
        assert plugin.called == 0
        assert result.plan.metadata["estimated_max_cost"] == pytest.approx(0.12)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_terminal_plan_cannot_be_reexecuted_with_a_new_dedupe_key(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        await _social_intention(core)
        first = await core.actions.execute_current(dedupe_key="terminal-first")
        assert first is not None and first.plan.status.value == "completed"
        execution_count = core.actions.store.execution_count()
        run_count = core.actions.store.run_count()
        event_count = core.database.event_count()

        replay = await core.actions.execute_plan(first.plan.id, dedupe_key="terminal-different-key")
        assert replay.deduplicated is True
        assert replay.plan.id == first.plan.id
        assert core.actions.store.execution_count() == execution_count
        assert core.actions.store.run_count() == run_count
        assert core.database.event_count() == event_count
    finally:
        core.close()
