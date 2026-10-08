from __future__ import annotations

import asyncio

from .models import MonitorOutcome, PlanStep, ToolCall, ToolExecutionResult, ToolExecutionStatus
from .plugins import ToolPlugin


class ExecutionMonitor:
    def __init__(self, *, verify_timeout_seconds: float = 2.0) -> None:
        if verify_timeout_seconds <= 0:
            raise ValueError("verify_timeout_seconds must be positive")
        self.verify_timeout_seconds = verify_timeout_seconds

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
        verify_timed_out = False
        try:
            plugin_ok = await asyncio.wait_for(
                plugin.verify(call.tool_name, dict(call.arguments), dict(result.output), call),
                timeout=self.verify_timeout_seconds,
            )
        except asyncio.TimeoutError:
            plugin_ok = False
            verify_timed_out = True
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
            result.error_code = "EFFECT_VERIFICATION_TIMEOUT" if verify_timed_out else "EXPECTED_EFFECT_NOT_VERIFIED"
            result.error_message = (
                "tool effect verification timed out"
                if verify_timed_out
                else "tool returned but expected effect was not verified"
            )
            result.retryable = outcome == MonitorOutcome.RETRY
        return outcome
