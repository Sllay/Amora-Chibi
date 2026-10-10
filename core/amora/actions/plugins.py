from __future__ import annotations

from collections.abc import Awaitable, Callable
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
    """In-process registry with explicit registration and runtime health.

    Manifest health is the startup/default state. Runtime health is mutable and
    deliberately lives in the registry so a transient integration failure never
    has to mutate a plugin manifest. Future connectors (Minecraft, OBS, browser,
    etc.) can stay UNAVAILABLE while disconnected and become HEALTHY on connect.
    """

    def __init__(self) -> None:
        self._plugins: dict[str, ToolPlugin] = {}
        self._health: dict[str, PluginHealth] = {}

    def register(self, plugin: ToolPlugin) -> None:
        manifest = plugin.manifest
        if manifest.plugin_id in self._plugins:
            raise ValueError(f"plugin already registered: {manifest.plugin_id}")
        tool_names = [tool.name for tool in manifest.tools]
        if len(tool_names) != len(set(tool_names)):
            raise ValueError(f"duplicate tool in plugin manifest: {manifest.plugin_id}")
        self._plugins[manifest.plugin_id] = plugin
        self._health[manifest.plugin_id] = manifest.health

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

    def health(self, plugin_id: str) -> PluginHealth:
        plugin = self._plugins.get(plugin_id)
        if plugin is None:
            return PluginHealth.UNAVAILABLE
        return self._health.get(plugin_id, plugin.manifest.health)

    def set_health(self, plugin_id: str, health: PluginHealth) -> None:
        if plugin_id not in self._plugins:
            raise KeyError(plugin_id)
        self._health[plugin_id] = health

    def health_snapshot(self) -> dict[str, str]:
        return {plugin_id: self.health(plugin_id).value for plugin_id in sorted(self._plugins)}

    @property
    def plugin_count(self) -> int:
        return len(self._plugins)

    @property
    def tool_count(self) -> int:
        return sum(len(plugin.manifest.tools) for plugin in self._plugins.values())


class CoreActionPlugin:
    """Safe local semantic actions consumed by presentation/action layers."""

    def __init__(
        self,
        events: AsyncEventBus,
        *,
        semantic_sink: Callable[[AmoraEvent], Awaitable[object]] | None = None,
    ) -> None:
        self.events = events
        self.semantic_sink = semantic_sink
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
                    input_schema={"type": "object", "required": ["intention_kind", "intention_id"]},
                    output_schema={"type": "object", "required": ["published", "event_id", "action_kind"]},
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
            return {"wait_registered": True, "semantic_only": True, "intention_id": str(call.intention_id)}
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
        if self.semantic_sink is not None:
            await self.semantic_sink(event)
        return {
            "published": True,
            "event_id": publish.event_id,
            "action_kind": action_kind,
            "target_id": target,
            "duplicate_event": publish.deduplicated,
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
