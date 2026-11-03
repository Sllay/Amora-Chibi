from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from amora.actions import (
    CapabilityBinding,
    CapabilityDeclaration,
    CapabilityDescriptor,
    CoreActionPlugin,
    ExecutionScope,
    PluginHealth,
    PluginManifest,
    PluginRegistry,
    RiskLevel,
    ToolCall,
    ToolSpec,
)
from amora.actions.runtime_capabilities import (
    resolve_runtime_capabilities,
    unbind_runtime_capabilities,
)
from amora.cognition.capability_context import CapabilityContextProjector
from amora.cognition.models import CognitiveContext, CognitiveRequest, ContextCapability
from amora.cognition.providers import GroqCognitiveProvider
from amora.events import AsyncEventBus


class _ExpressionPlugin:
    def __init__(
        self,
        *,
        suffix: str = "primary",
        summary: str = "Express a social reaction visually using a known transport-native expression.",
    ) -> None:
        self.plugin_id = f"discord.expression.{suffix}"
        self.capability_id = f"discord.expression.send.{suffix}"
        self.permission = f"discord:expression:{suffix}"
        self.summary = summary
        self._manifest = PluginManifest(
            plugin_id=self.plugin_id,
            name=f"Expression {suffix}",
            version="1",
            permissions={self.permission},
            tools=[
                ToolSpec(
                    name="send_expression",
                    description="Send a transport-native visual expression.",
                    permissions={self.permission},
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    cost=0.02,
                    risk=RiskLevel.LOW,
                    timeout_seconds=2.0,
                    idempotent=True,
                )
            ],
            cost=0.01,
            risk=RiskLevel.LOW,
            health=PluginHealth.HEALTHY,
            execution_scope=ExecutionScope.LOCAL,
        )

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    @property
    def capabilities(self) -> list[CapabilityDeclaration]:
        return [
            CapabilityDeclaration(
                descriptor=CapabilityDescriptor(
                    capability_id=self.capability_id,
                    semantic_action="express_visually",
                    summary=self.summary,
                    affordances={"visual_reaction", "social_expression", "sticker"},
                    examples=[
                        "abre esse sorrisão pro pai",
                        "manda uma figurinha engraçada",
                        "reage visualmente a isso",
                    ],
                    inputs={"expression_intent": "ExpressionIntent", "conversation": "ConversationRef"},
                    outputs={"receipt": "DeliveryReceipt"},
                    preconditions=["target_conversation_resolved", "expression_known"],
                    effects=["visual_expression_delivered"],
                    risk=RiskLevel.LOW,
                    cost=0.01,
                    may_refuse=True,
                    may_initiate=True,
                    external_side_effect=True,
                    verification_effect="message_delivered",
                ),
                binding=CapabilityBinding(plugin_id=self.plugin_id, tool_name="send_expression"),
            )
        ]

    async def execute(self, tool_name: str, arguments: dict[str, object], call: ToolCall) -> dict[str, object]:
        return {"sent": tool_name == "send_expression"}

    async def verify(
        self,
        tool_name: str,
        arguments: dict[str, object],
        output: dict[str, object],
        call: ToolCall,
    ) -> bool:
        return output.get("sent") is True


def _context(text: str, *, token_budget: int = 3200) -> CognitiveContext:
    return CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime.now(timezone.utc),
        speaker_id="ENTITY_BK",
        target_id="ENTITY_AMORA",
        user_text=text,
        token_budget=token_budget,
    )


def _request(text: str) -> CognitiveRequest:
    return CognitiveRequest(text=text, speaker_id="ENTITY_BK", target_id="ENTITY_AMORA")


def _runtime_with_expression(*, suffix: str = "primary", summary: str | None = None):
    events = AsyncEventBus()
    plugins = PluginRegistry()
    plugins.register(CoreActionPlugin(events))
    plugin = _ExpressionPlugin(suffix=suffix, summary=summary or _ExpressionPlugin().summary)
    plugins.register(plugin)
    return events, plugins, plugin


def test_core_action_registration_binds_same_registry_to_runtime_bus() -> None:
    events = AsyncEventBus()
    plugins = PluginRegistry()
    plugins.register(CoreActionPlugin(events))
    try:
        assert resolve_runtime_capabilities(events) is plugins.capability_registry
    finally:
        unbind_runtime_capabilities(events)


def test_runtime_capability_bindings_are_isolated_per_core() -> None:
    first_events = AsyncEventBus()
    second_events = AsyncEventBus()
    first = PluginRegistry()
    second = PluginRegistry()
    first.register(CoreActionPlugin(first_events))
    second.register(CoreActionPlugin(second_events))
    first.register(_ExpressionPlugin(suffix="first"))
    second.register(_ExpressionPlugin(suffix="second"))
    try:
        assert resolve_runtime_capabilities(first_events) is first.capability_registry
        assert resolve_runtime_capabilities(second_events) is second.capability_registry
        assert first.capability_registry is not second.capability_registry
        assert first.capability_registry.snapshot("discord.expression.send.first") is not None
        assert first.capability_registry.snapshot("discord.expression.send.second") is None
        assert second.capability_registry.snapshot("discord.expression.send.second") is not None
    finally:
        unbind_runtime_capabilities(first_events)
        unbind_runtime_capabilities(second_events)


def test_projector_selects_relevant_capability_without_leaking_execution_binding() -> None:
    events, plugins, plugin = _runtime_with_expression()
    try:
        result = CapabilityContextProjector(events).project(
            _request("iae amora, abre esse sorrisão pro pai"),
            _context("iae amora, abre esse sorrisão pro pai"),
        )
        assert result.capabilities
        assert result.capabilities[0].capability_id == plugin.capability_id
        assert result.capabilities[0].authority == "core_execution_gate"
        assert "CAPABILITY_CONTEXT_PROJECTED" in result.reason_codes
        assert "CAPABILITY_CONTEXT_NON_AUTHORITATIVE" in result.reason_codes

        serialized = str([item.model_dump(mode="json") for item in result.capabilities])
        assert "plugin_id" not in serialized
        assert "tool_name" not in serialized
        assert "permissions" not in serialized
        assert plugin.permission not in serialized
        assert plugin.plugin_id not in serialized
    finally:
        unbind_runtime_capabilities(events)


def test_projector_does_not_dump_irrelevant_catalog() -> None:
    events, _, _ = _runtime_with_expression()
    try:
        result = CapabilityContextProjector(events).project(
            _request("qual é a previsão do tempo amanhã?"),
            _context("qual é a previsão do tempo amanhã?"),
        )
        assert result.capabilities == []
        assert result.reason_codes == ["CAPABILITY_CONTEXT_EMPTY"]
    finally:
        unbind_runtime_capabilities(events)


def test_projector_respects_top_k_and_capability_token_budget() -> None:
    events = AsyncEventBus()
    plugins = PluginRegistry()
    plugins.register(CoreActionPlugin(events))
    for index in range(8):
        plugins.register(_ExpressionPlugin(suffix=f"candidate-{index}"))
    try:
        result = CapabilityContextProjector(events, max_items=3, token_budget=700).project(
            _request("reage visualmente com uma figurinha"),
            _context("reage visualmente com uma figurinha"),
        )
        assert 1 <= len(result.capabilities) <= 3
        assert result.estimated_tokens <= 700
        assert result.truncated is True
        assert "CAPABILITY_CONTEXT_TRIMMED" in result.reason_codes
    finally:
        unbind_runtime_capabilities(events)


def test_projector_bounds_long_descriptor_summary_before_context_validation() -> None:
    events, _, _ = _runtime_with_expression(summary="x" * 900)
    try:
        result = CapabilityContextProjector(events, token_budget=700).project(
            _request("manda uma figurinha engraçada"),
            _context("manda uma figurinha engraçada"),
        )
        assert result.capabilities
        assert len(result.capabilities[0].summary) <= 700
    finally:
        unbind_runtime_capabilities(events)


def test_provider_payload_contains_only_provider_safe_capability_projection() -> None:
    capability = ContextCapability(
        capability_id="discord.expression.send.primary",
        semantic_action="express_visually",
        summary="Express a visual reaction.",
        affordances=["visual_reaction", "sticker"],
        availability="available",
        effects=["visual_expression_delivered"],
        policy={"risk": "low", "cost": 0.01, "external_side_effect": True},
        verification_effect="message_delivered",
    )
    context = _context("manda uma figurinha")
    context.capabilities = [capability]

    payload = GroqCognitiveProvider._context_payload(context)
    assert payload["capabilities"][0]["capability_id"] == capability.capability_id
    assert payload["capabilities"][0]["authority"] == "core_execution_gate"
    serialized = str(payload["capabilities"])
    assert "plugin_id" not in serialized
    assert "tool_name" not in serialized
    assert "permissions" not in serialized


def test_unbound_runtime_projects_empty_without_failing_cognition() -> None:
    events = AsyncEventBus()
    result = CapabilityContextProjector(events).project(
        _request("manda uma figurinha"),
        _context("manda uma figurinha"),
    )
    assert result.capabilities == []
    assert result.reason_codes == ["CAPABILITY_REGISTRY_NOT_BOUND"]
