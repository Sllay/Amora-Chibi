from datetime import datetime, timezone
from uuid import uuid4

import pytest

from amora.actions import (
    ActionBus,
    ExecutionContext,
    PluginHealth,
    PluginManifest,
    PluginRegistry,
    RiskLevel,
    ToolCall,
    ToolExecutionStatus,
    ToolSpec,
)
from amora.time import SimulatedClock


class _NonIdempotentPlugin:
    def __init__(self) -> None:
        self.called = 0
        self._manifest = PluginManifest(
            plugin_id="test.non_idempotent",
            name="Non-idempotent test plugin",
            version="1",
            permissions={"test:write"},
            tools=[
                ToolSpec(
                    name="write_once",
                    permissions={"test:write"},
                    idempotent=False,
                    risk=RiskLevel.LOW,
                )
            ],
            health=PluginHealth.HEALTHY,
        )

    @property
    def manifest(self):
        return self._manifest

    async def execute(self, tool_name, arguments, call):
        self.called += 1
        return {"written": True}

    async def verify(self, tool_name, arguments, output, call):
        return output.get("written") is True


@pytest.mark.asyncio
async def test_non_idempotent_tool_requires_explicit_approval() -> None:
    clock = SimulatedClock(datetime(2026, 10, 8, 23, 50, tzinfo=timezone.utc))
    registry = PluginRegistry()
    plugin = _NonIdempotentPlugin()
    registry.register(plugin)
    bus = ActionBus(registry, clock)
    call = ToolCall(
        plan_id=uuid4(),
        step_id=uuid4(),
        intention_id=uuid4(),
        plugin_id="test.non_idempotent",
        tool_name="write_once",
        created_at=clock.now(),
    )

    denied = await bus.dispatch(
        call,
        ExecutionContext(granted_permissions={"test:write"}, max_risk=RiskLevel.LOW),
    )
    assert denied.status == ToolExecutionStatus.DENIED
    assert denied.error_code == "NON_IDEMPOTENT_APPROVAL_REQUIRED"
    assert plugin.called == 0

    approved = await bus.dispatch(
        call,
        ExecutionContext(
            granted_permissions={"test:write"},
            max_risk=RiskLevel.LOW,
            allow_non_idempotent=True,
        ),
    )
    assert approved.status == ToolExecutionStatus.SUCCEEDED
    assert plugin.called == 1
