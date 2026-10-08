from datetime import datetime, timezone
from uuid import uuid4

import pytest

from amora.actions import (
    ActionBus,
    ExecutionContext,
    ExecutionScope,
    PluginHealth,
    PluginManifest,
    PluginRegistry,
    RiskLevel,
    ToolCall,
    ToolExecutionStatus,
    ToolSpec,
)
from amora.app import AmoraCore
from amora.autonomy import DecisionContext, DriveType
from amora.events import PrivacyScope
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 8, 23, 30, tzinfo=timezone.utc))


async def _create_social_intention(core: AmoraCore):
    core.autonomy.adjust_drive(DriveType.SOCIAL, 0.55, reason="test social need", dedupe_key="v07-social-drive")
    result = await core.autonomy.cycle(
        DecisionContext(is_idle=True, available_entities={"PERSON_BK"}),
        dedupe_key="v07-social-cycle",
    )
    assert result.intention is not None
    return result.intention


@pytest.mark.asyncio
async def test_intention_becomes_verified_semantic_action(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        intention = await _create_social_intention(core)
        plan = await core.actions.plan_current(dedupe_key="v07-plan")
        assert plan is not None
        assert plan.intention_id == intention.id
        assert plan.skill_name == "core.semantic_intention"
        assert plan.steps[0].tool_name == "emit_intention"

        result = await core.actions.execute_current(dedupe_key="v07-execute")
        assert result is not None
        assert result.plan.status.value == "completed"
        assert result.monitor_outcome.value == "verified"
        assert len(result.executions) == 1
        assert result.executions[0].verified is True
        assert result.executions[0].output["action_kind"] == "initiate_contact"
        assert result.executions[0].output["target_id"] == "PERSON_BK"
        assert core.autonomy.store.current_intention() is None
        assert core.actions.store.plan_count() == 1
        assert core.actions.store.run_count() == 1
        assert core.actions.store.execution_count() == 1
    finally:
        core.close()


@pytest.mark.asyncio
async def test_action_run_replay_does_not_execute_tool_twice(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        await _create_social_intention(core)
        first = await core.actions.execute_current(dedupe_key="same-run")
        assert first is not None and first.deduplicated is False
        event_count = core.database.event_count()
        execution_count = core.actions.store.execution_count()

        replay = await core.actions.execute_plan(first.plan.id, dedupe_key="same-run")
        assert replay.deduplicated is True
        assert replay.plan.id == first.plan.id
        assert core.database.event_count() == event_count
        assert core.actions.store.execution_count() == execution_count
    finally:
        core.close()


@pytest.mark.asyncio
async def test_auth_scope_aborts_before_tool_execution(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        await _create_social_intention(core)
        plan = await core.actions.plan_current()
        assert plan is not None
        context = core.actions.default_context(PrivacyScope.AUTH)
        result = await core.actions.execute_plan(plan.id, dedupe_key="auth-denied", context=context)
        assert result.plan.status.value == "aborted"
        assert result.monitor_outcome.value == "abort"
        assert result.executions == []
        assert core.actions.store.execution_count() == 0
        assert "AUTH_EXECUTION_DENIED" in result.plan.reason_codes
    finally:
        core.close()


class _BrokenPlugin:
    def __init__(self, *, remote: bool = False, risk: RiskLevel = RiskLevel.LOW) -> None:
        self._manifest = PluginManifest(
            plugin_id="test.broken",
            name="Broken test plugin",
            version="1",
            permissions={"test:use"},
            tools=[ToolSpec(name="explode", permissions={"test:use"}, risk=risk, timeout_seconds=1.0)],
            health=PluginHealth.HEALTHY,
            risk=risk,
            execution_scope=ExecutionScope.REMOTE if remote else ExecutionScope.LOCAL,
        )
        self.called = 0

    @property
    def manifest(self):
        return self._manifest

    async def execute(self, tool_name, arguments, call):
        self.called += 1
        raise RuntimeError("boom SECRET_SHOULD_NOT_LEAK")

    async def verify(self, tool_name, arguments, output, call):
        return False


def _call(scope: PrivacyScope = PrivacyScope.PERSONAL) -> ToolCall:
    return ToolCall(
        plan_id=uuid4(),
        step_id=uuid4(),
        intention_id=uuid4(),
        plugin_id="test.broken",
        tool_name="explode",
        arguments={},
        privacy_scope=scope,
        created_at=_clock().now(),
    )


@pytest.mark.asyncio
async def test_plugin_failure_is_isolated_and_error_does_not_leak_exception_text() -> None:
    clock = _clock()
    registry = PluginRegistry()
    plugin = _BrokenPlugin()
    registry.register(plugin)
    bus = ActionBus(registry, clock)
    context = ExecutionContext(granted_permissions={"test:use"}, max_risk=RiskLevel.LOW)
    result = await bus.dispatch(_call(), context)
    assert result.status == ToolExecutionStatus.FAILED
    assert result.retryable is True
    assert "SECRET_SHOULD_NOT_LEAK" not in (result.error_message or "")
    assert plugin.called == 1


@pytest.mark.asyncio
async def test_permission_remote_and_risk_gates_run_before_plugin() -> None:
    clock = _clock()

    registry = PluginRegistry()
    permission_plugin = _BrokenPlugin()
    registry.register(permission_plugin)
    denied = await ActionBus(registry, clock).dispatch(_call(), ExecutionContext())
    assert denied.status == ToolExecutionStatus.DENIED
    assert denied.error_code == "PERMISSION_DENIED"
    assert permission_plugin.called == 0

    remote_registry = PluginRegistry()
    remote_plugin = _BrokenPlugin(remote=True)
    remote_registry.register(remote_plugin)
    remote_denied = await ActionBus(remote_registry, clock).dispatch(
        _call(),
        ExecutionContext(granted_permissions={"test:use"}, remote_processing_allowed=False),
    )
    assert remote_denied.error_code == "REMOTE_PROCESSING_NOT_ALLOWED"
    assert remote_plugin.called == 0

    risk_registry = PluginRegistry()
    risk_plugin = _BrokenPlugin(risk=RiskLevel.HIGH)
    risk_registry.register(risk_plugin)
    risk_denied = await ActionBus(risk_registry, clock).dispatch(
        _call(),
        ExecutionContext(granted_permissions={"test:use"}, max_risk=RiskLevel.LOW),
    )
    assert risk_denied.error_code == "RISK_LIMIT_EXCEEDED"
    assert risk_plugin.called == 0
