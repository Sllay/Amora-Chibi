from __future__ import annotations

from .models import MonitorOutcome, PlanStep, ToolCall, ToolExecutionResult, ToolExecutionStatus
from .plugins import ToolPlugin


class ExecutionMonitor:
    async def evaluate(
        self,
        step: PlanStep,
        result: ToolExecutionResult,
        plugin: ToolPlugin | None,
        call: ToolCall,
    ) -> MonitorOutcome:
        if result.status == ToolExecutionStatus.DENIED:
            result.verified = False
            result.monitor_outcome = MonitorOutcome.ABORT
            return MonitorOutcome.ABORT
        if result.status == ToolExecutionStatus.TIMED_OUT:
            can_retry = step.retry_policy.retry_on_timeout and result.attempt < step.retry_policy.max_attempts
            outcome = MonitorOutcome.RETRY if can_retry else step.retry_policy.on_exhausted
            result.verified = False
            result.monitor_outcome = outcome
            return outcome
        if result.status == ToolExecutionStatus.FAILED:
            can_retry = step.retry_policy.retry_on_failure and result.attempt < step.retry_policy.max_attempts
            outcome = MonitorOutcome.RETRY if can_retry else step.retry_policy.on_exhausted
            result.verified = False
            result.monitor_outcome = outcome
            return outcome
        if plugin is None:
            result.verified = False
            result.monitor_outcome = MonitorOutcome.ABORT
            return MonitorOutcome.ABORT

        generic_ok = all(result.output.get(key) == expected for key, expected in step.expected_output.items())
        try:
            plugin_ok = await plugin.verify(call.tool_name, dict(call.arguments), dict(result.output), call)
        except Exception:
            plugin_ok = False
        verified = generic_ok and plugin_ok
        result.verified = verified
        if verified:
            result.monitor_outcome = MonitorOutcome.VERIFIED
            return MonitorOutcome.VERIFIED
        outcome = MonitorOutcome.RETRY if result.attempt < step.retry_policy.max_attempts else step.retry_policy.on_exhausted
        result.monitor_outcome = outcome
        if result.error_code is None:
            result.error_code = "EXPECTED_EFFECT_NOT_VERIFIED"
            result.error_message = "tool returned but expected effect was not verified"
            result.retryable = outcome == MonitorOutcome.RETRY
        return outcome
