from __future__ import annotations

from uuid import NAMESPACE_URL, UUID, uuid5

from amora.autonomy import AutonomyEngine, IntentionStatus
from amora.emotion import EmotionEngine, SocialBidStatus
from amora.events import AmoraEvent, AsyncEventBus, PrivacyScope
from amora.time import Clock

from .bus import ActionBus
from .models import (
    ActionPlan,
    ExecutionContext,
    MonitorOutcome,
    PlanExecutionResult,
    PlanStatus,
    RiskLevel,
    StepStatus,
    ToolCall,
    ToolExecutionStatus,
)
from .monitor import ExecutionMonitor
from .planner import Planner
from .plugins import CoreActionPlugin, PluginRegistry
from .skills import SkillLibrary
from .store import ActionStore


class ActionEngine:
    """V0.7 boundary: intention -> plan -> guarded tools -> verified effect."""

    DEFAULT_PERMISSIONS = {"core:action:semantic"}

    def __init__(
        self,
        store: ActionStore,
        clock: Clock,
        autonomy: AutonomyEngine,
        emotion: EmotionEngine,
        events: AsyncEventBus,
        *,
        registry: PluginRegistry | None = None,
        skills: SkillLibrary | None = None,
        planner: Planner | None = None,
    ) -> None:
        self.store = store
        self.clock = clock
        self.autonomy = autonomy
        self.emotion = emotion
        self.events = events
        self.registry = registry or PluginRegistry()
        if self.registry.plugin("core.actions") is None:
            self.registry.register(CoreActionPlugin(events))
        self.skills = skills or SkillLibrary.builtins()
        self.planner = planner or Planner(clock, self.skills)
        self.bus = ActionBus(self.registry, clock)
        self.monitor = ExecutionMonitor()

    def close(self) -> None:
        self.store.close()

    def default_context(self, privacy_scope: PrivacyScope = PrivacyScope.PERSONAL) -> ExecutionContext:
        return ExecutionContext(
            granted_permissions=set(self.DEFAULT_PERMISSIONS),
            privacy_scope=privacy_scope,
            remote_processing_allowed=False,
            max_risk=RiskLevel.LOW,
            max_total_cost=0.10,
        )

    def state(self) -> dict[str, object]:
        return {
            "plugin_count": self.registry.plugin_count,
            "tool_count": self.registry.tool_count,
            "skill_count": self.skills.count,
            "plan_count": self.store.plan_count(),
            "action_run_count": self.store.run_count(),
            "tool_execution_count": self.store.execution_count(),
            "plugins": [item.model_dump(mode="json") for item in self.registry.manifests()],
            "skills": [item.model_dump(mode="json") for item in self.skills.list()],
        }

    async def plan_current(self, *, dedupe_key: str | None = None) -> ActionPlan | None:
        if dedupe_key:
            replay = self.store.get_plan_by_dedupe(dedupe_key)
            if replay is not None:
                return replay
        intention = self.autonomy.store.current_intention()
        if intention is None:
            return None
        existing = self.store.latest_for_intention(intention.id)
        if existing is not None and existing.status in {PlanStatus.PENDING, PlanStatus.RUNNING}:
            return existing
        plan = await self.planner.plan(intention, dedupe_key=dedupe_key)
        if plan is None:
            return None
        if not self.store.put_plan(plan):
            if dedupe_key:
                return self.store.get_plan_by_dedupe(dedupe_key)
            raise RuntimeError("plan persistence failed")
        await self.events.publish(
            AmoraEvent(
                event_type="action.plan_created",
                source="action_engine",
                actor="ENTITY_AMORA",
                target=plan.target_id,
                importance=0.55,
                payload={
                    "plan_id": str(plan.id),
                    "intention_id": str(plan.intention_id),
                    "planner_mode": plan.planner_mode.value,
                    "skill_name": plan.skill_name,
                    "step_count": len(plan.steps),
                },
                privacy_scope=PrivacyScope.PERSONAL,
                dedupe_key=f"action.plan:{plan.id}",
            )
        )
        return plan

    async def execute_current(
        self,
        *,
        dedupe_key: str | None = None,
        context: ExecutionContext | None = None,
    ) -> PlanExecutionResult | None:
        if dedupe_key:
            replay = self.store.get_run_by_dedupe(dedupe_key)
            if replay is not None:
                return replay
        intention = self.autonomy.store.current_intention()
        if intention is None:
            return None
        plan = self.store.latest_for_intention(intention.id)
        if plan is None or plan.status not in {PlanStatus.PENDING, PlanStatus.RUNNING}:
            plan = await self.plan_current(dedupe_key=f"plan:{dedupe_key}" if dedupe_key else None)
        if plan is None:
            return None
        return await self.execute_plan(plan.id, dedupe_key=dedupe_key, context=context)

    async def execute_plan(
        self,
        plan_id: UUID | str,
        *,
        dedupe_key: str | None = None,
        context: ExecutionContext | None = None,
    ) -> PlanExecutionResult:
        if dedupe_key:
            replay = self.store.get_run_by_dedupe(dedupe_key)
            if replay is not None:
                return replay
        plan = self.store.get_plan(plan_id)
        if plan is None:
            raise KeyError(f"unknown plan: {plan_id}")
        active_context = context or self.default_context()
        if active_context.privacy_scope == PrivacyScope.AUTH:
            plan.status = PlanStatus.ABORTED
            plan.updated_at = self.clock.now()
            plan.reason_codes.append("AUTH_EXECUTION_DENIED")
            self.store.put_plan(plan)
            terminal = PlanExecutionResult(plan=plan, executions=[], monitor_outcome=MonitorOutcome.ABORT)
            self._persist_terminal(terminal, dedupe_key)
            return terminal

        plan.status = PlanStatus.RUNNING
        plan.updated_at = self.clock.now()
        self.store.put_plan(plan)
        executions = []
        final_outcome = MonitorOutcome.VERIFIED

        for step in sorted(plan.steps, key=lambda item: item.order):
            step.status = StepStatus.RUNNING
            plan.updated_at = self.clock.now()
            self.store.put_plan(plan)
            stable_call_id = uuid5(NAMESPACE_URL, f"amora:{plan.id}:{step.id}")
            step_outcome = MonitorOutcome.ABORT

            for attempt in range(1, step.retry_policy.max_attempts + 1):
                call = ToolCall(
                    id=stable_call_id,
                    plan_id=plan.id,
                    step_id=step.id,
                    intention_id=plan.intention_id,
                    plugin_id=step.plugin_id,
                    tool_name=step.tool_name,
                    arguments=dict(step.arguments),
                    privacy_scope=active_context.privacy_scope,
                    dedupe_key=f"{dedupe_key}:{step.id}" if dedupe_key else f"plan:{plan.id}:step:{step.id}",
                    created_at=self.clock.now(),
                )
                result = await self.bus.dispatch(call, active_context, attempt=attempt)
                resolved = self.registry.resolve_tool(step.plugin_id, step.tool_name)
                plugin = resolved[0] if resolved else None
                step_outcome = await self.monitor.evaluate(step, result, plugin, call)
                step.attempt_count = attempt
                step.last_error_code = result.error_code
                executions.append(result)
                self.store.put_execution(result)
                if step_outcome == MonitorOutcome.VERIFIED:
                    step.status = StepStatus.SUCCEEDED
                    break
                if step_outcome == MonitorOutcome.RETRY:
                    continue
                step.status = (
                    StepStatus.DENIED
                    if result.status == ToolExecutionStatus.DENIED
                    else StepStatus.TIMED_OUT
                    if result.status == ToolExecutionStatus.TIMED_OUT
                    else StepStatus.FAILED
                )
                break

            plan.updated_at = self.clock.now()
            self.store.put_plan(plan)
            if step_outcome != MonitorOutcome.VERIFIED:
                final_outcome = step_outcome
                plan.status = PlanStatus.ABORTED if step_outcome in {MonitorOutcome.ABORT, MonitorOutcome.ASK} else PlanStatus.FAILED
                plan.reason_codes.append(f"EXECUTION_{step_outcome.value.upper()}")
                break

        if final_outcome == MonitorOutcome.VERIFIED:
            plan.status = PlanStatus.COMPLETED
            plan.reason_codes.extend(["ALL_STEPS_VERIFIED", "INTENTION_EXECUTED"])
            self.autonomy.store.set_intention_status(plan.intention_id, IntentionStatus.COMPLETED)
            intention = self._intention_for_plan(plan)
            if intention is not None and intention.source_kind == "social_bid" and intention.source_ref_id:
                self.emotion.store.set_bid_status(intention.source_ref_id, SocialBidStatus.CONSUMED)

        plan.updated_at = self.clock.now()
        self.store.put_plan(plan)
        terminal = PlanExecutionResult(plan=plan, executions=executions, monitor_outcome=final_outcome)
        terminal = self._persist_terminal(terminal, dedupe_key)
        await self._publish_terminal(terminal)
        return terminal

    def _intention_for_plan(self, plan: ActionPlan):
        # The plan references a durable autonomy row; listing current intention is insufficient
        # after completion, so hydrate directly from the plan's source when still active first.
        current = self.autonomy.store.current_intention()
        if current is not None and current.id == plan.intention_id:
            return current
        # V0.7 only needs source metadata before marking completion; plans copy target/kind but
        # not source refs, so fall back to the latest known current row when unavailable.
        return None

    def _persist_terminal(self, result: PlanExecutionResult, dedupe_key: str | None) -> PlanExecutionResult:
        if self.store.put_run(result, dedupe_key=dedupe_key):
            return result
        if dedupe_key:
            replay = self.store.get_run_by_dedupe(dedupe_key)
            if replay is not None:
                return replay
        raise RuntimeError("action run persistence failed")

    async def _publish_terminal(self, result: PlanExecutionResult) -> None:
        await self.events.publish(
            AmoraEvent(
                event_type="action.execution_completed" if result.plan.status == PlanStatus.COMPLETED else "action.execution_failed",
                source="action_engine",
                actor="ENTITY_AMORA",
                target=result.plan.target_id,
                importance=0.75 if result.plan.status == PlanStatus.COMPLETED else 0.85,
                payload={
                    "plan_id": str(result.plan.id),
                    "intention_id": str(result.plan.intention_id),
                    "status": result.plan.status.value,
                    "monitor_outcome": result.monitor_outcome.value,
                    "execution_count": len(result.executions),
                    "reason_codes": result.plan.reason_codes,
                },
                privacy_scope=PrivacyScope.PERSONAL,
                dedupe_key=f"action.terminal:{result.plan.id}:{result.plan.status.value}",
            )
        )
