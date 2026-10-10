from __future__ import annotations

import asyncio
import logging

from amora.events import PrivacyScope
from amora.time import Clock

from .models import ExecutionContext, ExecutionScope, PluginHealth, ToolCall, ToolExecutionResult, ToolExecutionStatus
from .plugins import PluginRegistry
from .schema import validate_schema

logger = logging.getLogger("amora.actions")


class ActionBus:
    """Permission/privacy/risk/cost/idempotency/schema gate and isolated tool boundary."""

    def __init__(self, registry: PluginRegistry, clock: Clock) -> None:
        self.registry = registry
        self.clock = clock

    async def dispatch(self, call: ToolCall, context: ExecutionContext, *, attempt: int = 1) -> ToolExecutionResult:
        started = self.clock.now()
        resolved = self.registry.resolve_tool(call.plugin_id, call.tool_name)
        if resolved is None:
            return self._result(call, started, ToolExecutionStatus.DENIED, attempt, error_code="TOOL_NOT_REGISTERED", error_message="tool is not registered")
        plugin, spec = resolved
        manifest = plugin.manifest
        if self.registry.health(call.plugin_id) == PluginHealth.UNAVAILABLE:
            return self._result(call, started, ToolExecutionStatus.FAILED, attempt, error_code="PLUGIN_UNAVAILABLE", error_message="plugin is unavailable", retryable=True)
        if call.privacy_scope == PrivacyScope.AUTH:
            return self._result(call, started, ToolExecutionStatus.DENIED, attempt, error_code="AUTH_CONTEXT_DENIED", error_message="AUTH content cannot enter tool execution")
        if manifest.execution_scope == ExecutionScope.REMOTE and not context.remote_processing_allowed:
            return self._result(call, started, ToolExecutionStatus.DENIED, attempt, error_code="REMOTE_PROCESSING_NOT_ALLOWED", error_message="remote processing permission is required")
        if not spec.idempotent and not context.allow_non_idempotent:
            return self._result(call, started, ToolExecutionStatus.DENIED, attempt, error_code="NON_IDEMPOTENT_APPROVAL_REQUIRED", error_message="non-idempotent tool requires explicit execution approval")
        required_permissions = set(manifest.permissions) | set(spec.permissions)
        if not required_permissions.issubset(context.granted_permissions):
            return self._result(call, started, ToolExecutionStatus.DENIED, attempt, error_code="PERMISSION_DENIED", error_message="tool permission requirements are not satisfied")
        effective_risk = max(manifest.risk, spec.risk)
        if effective_risk > context.max_risk:
            return self._result(call, started, ToolExecutionStatus.DENIED, attempt, error_code="RISK_LIMIT_EXCEEDED", error_message="tool risk exceeds execution context")
        effective_cost = min(1.0, manifest.cost + spec.cost)
        if effective_cost > context.max_total_cost:
            return self._result(call, started, ToolExecutionStatus.DENIED, attempt, error_code="COST_LIMIT_EXCEEDED", error_message="tool cost exceeds execution context")
        if validate_schema(call.arguments, spec.input_schema):
            return self._result(call, started, ToolExecutionStatus.DENIED, attempt, error_code="INPUT_SCHEMA_INVALID", error_message="tool arguments do not satisfy the declared input schema")
        timeout = min(manifest.timeout_seconds, spec.timeout_seconds)
        try:
            output = await asyncio.wait_for(plugin.execute(call.tool_name, dict(call.arguments), call), timeout=timeout)
        except asyncio.TimeoutError:
            self.registry.set_health(call.plugin_id, PluginHealth.DEGRADED)
            return self._result(call, started, ToolExecutionStatus.TIMED_OUT, attempt, error_code="TOOL_TIMEOUT", error_message="tool execution timed out", retryable=True)
        except Exception as exc:
            self.registry.set_health(call.plugin_id, PluginHealth.DEGRADED)
            return self._result(call, started, ToolExecutionStatus.FAILED, attempt, error_code=f"PLUGIN_EXCEPTION_{type(exc).__name__.upper()}", error_message="plugin execution failed in isolation", retryable=True)
        if not isinstance(output, dict):
            self.registry.set_health(call.plugin_id, PluginHealth.DEGRADED)
            return self._result(call, started, ToolExecutionStatus.FAILED, attempt, error_code="OUTPUT_NOT_OBJECT", error_message="plugin output must be a JSON object")
        if validate_schema(output, spec.output_schema):
            self.registry.set_health(call.plugin_id, PluginHealth.DEGRADED)
            return self._result(call, started, ToolExecutionStatus.FAILED, attempt, error_code="OUTPUT_SCHEMA_INVALID", error_message="plugin output does not satisfy the declared output schema")
        self.registry.set_health(call.plugin_id, PluginHealth.HEALTHY)
        return self._result(call, started, ToolExecutionStatus.SUCCEEDED, attempt, output=output)

    def _result(self, call: ToolCall, started, status: ToolExecutionStatus, attempt: int, *, output: dict[str, object] | None = None, error_code: str | None = None, error_message: str | None = None, retryable: bool = False) -> ToolExecutionResult:
        result = ToolExecutionResult(call_id=call.id, plan_id=call.plan_id, step_id=call.step_id, plugin_id=call.plugin_id, tool_name=call.tool_name, status=status, attempt=attempt, output=output or {}, error_code=error_code, error_message=error_message, retryable=retryable, started_at=started, finished_at=self.clock.now(), dedupe_key=call.dedupe_key)
        log = logger.info if status == ToolExecutionStatus.SUCCEEDED else logger.warning
        log("tool_call call_id=%s plan_id=%s plugin=%s tool=%s status=%s attempt=%s error_code=%s", call.id, call.plan_id, call.plugin_id, call.tool_name, status.value, attempt, error_code)
        return result
