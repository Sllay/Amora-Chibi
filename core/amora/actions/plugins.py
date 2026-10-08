from __future__ import annotations

from typing import Protocol

from amora.events import AmoraEvent, AsyncEventBus

from .models import (
    ExecutionScope,
    PluginHealth,
    PluginManifest,
    RiskLevel,
    ToolCall,
    ToolSpec,
)


class ToolPlugin(Protocol):
    @property
    def manifest(self) -> PluginManifest: ...

    async def execute(self, tool_name: str, arguments: dict[str, object], call: ToolCall) -> dict[str, object]: ...

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, object],
        output: dict[str, object],
        call: ToolCall,
    ) -> bool: ...


class PluginRegistry:
    """In-process V0.7 registry.

    Plugins are registered explicitly; discovery/loading from arbitrary filesystem paths is
    intentionally out of scope. A broken plugin is isolated by ActionBus and cannot replace
    canonical Core state.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, ToolPlugin] = {}

    def register(self, plugin: ToolPlugin) -> None:
        manifest = plugin.manifest
        if manifest.plugin_id in self._plugins:
            raise ValueError(f"plugin already registered: {manifest.plugin_id}")
        tool_names = [tool.name for tool in manifest.tools]
        if len(tool_names) != len(set(tool_names)):
            raise ValueError(f"duplicate tool in plugin manifest: {manifest.plugin_id}")
        self._plugins[manifest.plugin_id] = plugin

    def plugin(self, plugin_id: str) -> ToolPlugin | None:
        return self._plugins.get(plugin_id)

    def resolve_tool(self, plugin_id: str, tool_name: str) -> tuple[ToolPlugin, ToolSpec] | None:
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            return None
        spec = next((tool for tool in plugin.manifest.tools if tool.name == tool_name), None)
        if spec is None:
            return None
        return plugin, spec

    def manifests(self) -> list[PluginManifest]:
        return sorted((plugin.manifest for plugin in self._plugins.values()), key=lambda item: item.plugin_id)

    @property
    def plugin_count(self) -> int:
        return len(self._plugins)

    @property
    def tool_count(self) -> int:
        return sum(len(plugin.manifest.tools) for plugin in self._plugins.values())


class CoreActionPlugin:
    """Safe local semantic actions consumed by later presentation/action layers.

    V0.7 can execute the decision pipeline without pretending that V0.8 voice or
    platform-specific integrations already exist.
    """

    def __init__(self, events: AsyncEventBus) -> None:
        self.events = events
        self._manifest = PluginManifest(
            plugin_id="core.actions",
            name="Amora Core Actions",
            version="1",
            permissions={"core:action:semantic"},
            events=["action.semantic_request"],
            tools=[
                ToolSpec(
                    name="emit_intention",
                    description="Emit a verified semantic action request for downstream action/voice layers.",
                    permissions={"core:action:semantic"},
                    input_schema={
                        "type": "object",
                        "required": ["intention_kind", "intention_id"],
                    },
                    output_schema={
                        "type": "object",
                        "required": ["published", "event_id", "action_kind"],
                    },
                    cost=0.0,
                    risk=RiskLevel.LOW,
                    timeout_seconds=3.0,
                    idempotent=True,
                ),
                ToolSpec(
                    name="wait",
                    description="Resolve a rest/wait intention locally without sleeping the Core thread.",
                    permissions=set(),
                    input_schema={"type": "object"},
                    output_schema={"type": "object", "required": ["wait_registered"]},
                    cost=0.0,
                    risk=RiskLevel.LOW,
                    timeout_seconds=1.0,
                    idempotent=True,
                ),
            ],
            schemas={},
            cost=0.0,
            risk=RiskLevel.LOW,
            timeout_seconds=3.0,
            health=PluginHealth.HEALTHY,
            execution_scope=ExecutionScope.LOCAL,
        )

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    async def execute(self, tool_name: str, arguments: dict[str, object], call: ToolCall) -> dict[str, object]:
        if tool_name == "wait":
            return {
                "wait_registered": True,
                "semantic_only": True,
                "intention_id": str(call.intention_id),
            }
        if tool_name != "emit_intention":
            raise KeyError(tool_name)

        action_kind = str(arguments.get("intention_kind") or "unknown")
        target_id = arguments.get("target_id")
        target = str(target_id) if target_id is not None else None
        event = AmoraEvent(
            event_type="action.semantic_request",
            source="plugin:core.actions",
            actor="ENTITY_AMORA",
            target=target,
            confidence=1.0,
            importance=0.7,
            payload={
                "action_kind": action_kind,
                "intention_id": str(call.intention_id),
                "plan_id": str(call.plan_id),
                "step_id": str(call.step_id),
            },
            privacy_scope=call.privacy_scope,
            dedupe_key=f"action.semantic:{call.id}",
        )
        publish = await self.events.publish(event)
        return {
            "published": bool(publish.accepted or publish.duplicate),
            "event_id": str(event.id),
            "action_kind": action_kind,
            "target_id": target,
            "duplicate_event": publish.duplicate,
        }

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, object],
        output: dict[str, object],
        call: ToolCall,
    ) -> bool:
        if tool_name == "wait":
            return output.get("wait_registered") is True
        if tool_name == "emit_intention":
            return (
                output.get("published") is True
                and output.get("action_kind") == arguments.get("intention_kind")
                and bool(output.get("event_id"))
            )
        return False
