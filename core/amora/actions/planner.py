from __future__ import annotations

from typing import Protocol

from amora.autonomy import Intention
from amora.time import Clock

from .models import ActionPlan, PlanStep, PlannerMode
from .skills import SkillLibrary, render_template


class SemanticPlanner(Protocol):
    async def plan(self, intention: Intention) -> ActionPlan | None: ...


class Planner:
    """Planner hierarchy for V0.7.

    Known/simple intentions are compiled locally from reusable skills. Semantic planning
    has an explicit interface but is never faked when no planner implementation is wired.
    """

    def __init__(
        self,
        clock: Clock,
        skills: SkillLibrary,
        semantic_planner: SemanticPlanner | None = None,
    ) -> None:
        self.clock = clock
        self.skills = skills
        self.semantic_planner = semantic_planner

    async def plan(self, intention: Intention, *, dedupe_key: str | None = None) -> ActionPlan | None:
        skill = self.skills.for_intention(intention.kind.value)
        if skill is not None:
            now = self.clock.now()
            steps = [
                PlanStep(
                    order=index,
                    plugin_id=template.plugin_id,
                    tool_name=template.tool_name,
                    arguments=render_template(template.arguments, intention),
                    expected_output=render_template(template.expected_output, intention),
                    retry_policy=template.retry_policy,
                )
                for index, template in enumerate(skill.steps)
            ]
            return ActionPlan(
                intention_id=intention.id,
                intention_kind=intention.kind.value,
                target_id=intention.target_id,
                skill_name=skill.name,
                planner_mode=skill.planner_mode,
                steps=steps,
                reason_codes=["LOCAL_SKILL_MATCH", f"PLANNER_{skill.planner_mode.value.upper()}"],
                created_at=now,
                updated_at=now,
                dedupe_key=dedupe_key,
                metadata={
                    "skill_version": skill.version,
                    "provider_used": False,
                    "source_kind": intention.source_kind,
                    "source_ref_id": intention.source_ref_id,
                },
            )

        if self.semantic_planner is None:
            return None
        plan = await self.semantic_planner.plan(intention)
        if plan is not None:
            plan.planner_mode = PlannerMode.LLM
            if "SEMANTIC_PLANNER" not in plan.reason_codes:
                plan.reason_codes.append("SEMANTIC_PLANNER")
        return plan
