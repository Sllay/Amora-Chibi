from __future__ import annotations

from typing import Any

from amora.autonomy import Intention

from .models import PlannerMode, RetryPolicy, SkillDefinition, ToolTemplate


class SkillLibrary:
    def __init__(self) -> None:
        self._skills: dict[str, SkillDefinition] = {}
        self._by_intention: dict[str, str] = {}

    def register(self, skill: SkillDefinition) -> None:
        if skill.name in self._skills:
            raise ValueError(f"skill already registered: {skill.name}")
        for intention_kind in skill.intention_kinds:
            if intention_kind in self._by_intention:
                raise ValueError(f"intention kind already handled by skill: {intention_kind}")
        self._skills[skill.name] = skill
        for intention_kind in skill.intention_kinds:
            self._by_intention[intention_kind] = skill.name

    def get(self, name: str) -> SkillDefinition | None:
        return self._skills.get(name)

    def for_intention(self, intention_kind: str) -> SkillDefinition | None:
        name = self._by_intention.get(intention_kind)
        return self._skills.get(name) if name else None

    def list(self) -> list[SkillDefinition]:
        return sorted(self._skills.values(), key=lambda item: item.name)

    @property
    def count(self) -> int:
        return len(self._skills)

    @classmethod
    def builtins(cls) -> "SkillLibrary":
        library = cls()
        semantic_kinds = {
            "social_bid",
            "initiate_contact",
            "observe",
            "ask_question",
            "play",
            "seek_novelty",
            "express",
            "practice",
            "seek_repair",
            "express_gratitude",
            "set_boundary",
            "withdraw",
        }
        library.register(
            SkillDefinition(
                name="core.semantic_intention",
                version="1",
                intention_kinds=semantic_kinds,
                planner_mode=PlannerMode.HTN,
                description="Translate a committed BDI intention into a semantic Action Bus request.",
                steps=[
                    ToolTemplate(
                        plugin_id="core.actions",
                        tool_name="emit_intention",
                        arguments={
                            "intention_kind": "$intention_kind",
                            "intention_id": "$intention_id",
                            "target_id": "$target_id",
                            "source_kind": "$source_kind",
                            "source_ref_id": "$source_ref_id",
                        },
                        expected_output={
                            "published": True,
                            "action_kind": "$intention_kind",
                        },
                        retry_policy=RetryPolicy(max_attempts=2),
                    )
                ],
            )
        )
        library.register(
            SkillDefinition(
                name="core.rest",
                version="1",
                intention_kinds={"rest", "wait"},
                planner_mode=PlannerMode.RULE,
                description="Resolve a rest/wait intention locally without blocking the event loop.",
                steps=[
                    ToolTemplate(
                        plugin_id="core.actions",
                        tool_name="wait",
                        arguments={"intention_id": "$intention_id"},
                        expected_output={"wait_registered": True},
                        retry_policy=RetryPolicy(max_attempts=1),
                    )
                ],
            )
        )
        return library


def render_template(value: Any, intention: Intention) -> Any:
    tokens = {
        "$intention_kind": intention.kind.value,
        "$intention_id": str(intention.id),
        "$target_id": intention.target_id,
        "$source_kind": intention.source_kind,
        "$source_ref_id": intention.source_ref_id,
    }
    if isinstance(value, str) and value in tokens:
        return tokens[value]
    if isinstance(value, dict):
        return {str(key): render_template(item, intention) for key, item in value.items()}
    if isinstance(value, list):
        return [render_template(item, intention) for item in value]
    return value
