from __future__ import annotations

from amora.actions import (
    CapabilityAvailabilityState,
    CapabilityBinding,
    CapabilityDeclaration,
    CapabilityDescriptor,
    CapabilityQuery,
    CapabilityRegistry,
    ExecutionContext,
    ExecutionScope,
    PluginHealth,
    PluginManifest,
    PluginRegistry,
    RiskLevel,
    ToolCall,
    ToolSpec,
)


class _CapabilityPlugin:
    def __init__(
        self,
        *,
        plugin_id: str = "discord.expression",
        capability_id: str = "discord.expression.send",
        execution_scope: ExecutionScope = ExecutionScope.LOCAL,
        risk: RiskLevel = RiskLevel.LOW,
        permission: str = "discord:expression:send",
    ) -> None:
        self._capability_id = capability_id
        self._manifest = PluginManifest(
            plugin_id=plugin_id,
            name=f"Plugin {plugin_id}",
            version="1",
            permissions={permission},
            tools=[
                ToolSpec(
                    name="send_expression",
                    description="Send a transport-native visual expression.",
                    permissions={permission},
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    cost=0.04,
                    risk=risk,
                    timeout_seconds=2.0,
                    idempotent=True,
                ),
                ToolSpec(
                    name="internal_sync",
                    description="Internal transport synchronization helper.",
                    permissions=set(),
                    input_schema={"type": "object"},
                    output_schema={"type": "object"},
                    cost=0.0,
                    risk=RiskLevel.LOW,
                    timeout_seconds=1.0,
                    idempotent=True,
                ),
            ],
            cost=0.01,
            risk=RiskLevel.LOW,
            health=PluginHealth.HEALTHY,
            execution_scope=execution_scope,
        )

    @property
    def manifest(self) -> PluginManifest:
        return self._manifest

    @property
    def capabilities(self) -> list[CapabilityDeclaration]:
        return [
            CapabilityDeclaration(
                descriptor=CapabilityDescriptor(
                    capability_id=self._capability_id,
                    semantic_action="express_visually",
                    summary="Express a social reaction visually using a known transport-native expression.",
                    affordances={"visual_reaction", "social_expression", "send_expression"},
                    examples=[
                        "abre esse sorrisão pro pai",
                        "manda uma figurinha engraçada",
                        "reage visualmente a isso",
                    ],
                    inputs={"expression_intent": "ExpressionIntent", "conversation": "ConversationRef"},
                    outputs={"receipt": "DeliveryReceipt"},
                    preconditions=["target_conversation_resolved", "expression_known"],
                    effects=["visual_expression_delivered"],
                    permissions=set(),
                    risk=RiskLevel.LOW,
                    cost=0.02,
                    may_refuse=True,
                    may_initiate=True,
                    external_side_effect=True,
                    verification_effect="message_delivered",
                ),
                binding=CapabilityBinding(
                    plugin_id=self._manifest.plugin_id,
                    tool_name="send_expression",
                ),
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
        return bool(output.get("sent"))


def _context(
    *,
    permissions: set[str] | None = None,
    remote: bool = False,
    max_risk: RiskLevel = RiskLevel.LOW,
    max_cost: float = 0.25,
) -> ExecutionContext:
    return ExecutionContext(
        granted_permissions=permissions or set(),
        remote_processing_allowed=remote,
        max_risk=max_risk,
        max_total_cost=max_cost,
    )


def test_registry_only_exposes_declared_capabilities_and_keeps_binding_local() -> None:
    plugins = PluginRegistry()
    plugin = _CapabilityPlugin()
    plugins.register(plugin)
    registry = CapabilityRegistry(plugins)

    assert registry.capability_count == 1
    snapshot = registry.snapshot(
        "discord.expression.send",
        _context(permissions={"discord:expression:send"}, max_risk=RiskLevel.MEDIUM),
    )
    assert snapshot is not None
    assert snapshot.availability == CapabilityAvailabilityState.AVAILABLE

    projection = snapshot.cognitive_projection()
    assert projection["capability_id"] == "discord.expression.send"
    assert projection["semantic_action"] == "express_visually"
    assert "plugin_id" not in projection
    assert "tool_name" not in projection
    assert "permissions" not in projection
    assert "internal_sync" not in str(projection)

    binding = registry.binding("discord.expression.send")
    assert binding is not None
    assert binding.plugin_id == "discord.expression"
    assert binding.tool_name == "send_expression"


def test_registry_is_live_for_plugin_registration_and_health() -> None:
    plugins = PluginRegistry()
    registry = CapabilityRegistry(plugins)
    assert registry.capability_count == 0

    plugin = _CapabilityPlugin()
    plugins.register(plugin)
    context = _context(permissions={"discord:expression:send"})

    assert registry.snapshot("discord.expression.send", context).availability == CapabilityAvailabilityState.AVAILABLE

    plugins.set_health("discord.expression", PluginHealth.DEGRADED)
    assert registry.snapshot("discord.expression.send", context).availability == CapabilityAvailabilityState.DEGRADED

    plugins.set_health("discord.expression", PluginHealth.UNAVAILABLE)
    assert registry.snapshot("discord.expression.send", context).availability == CapabilityAvailabilityState.UNAVAILABLE


def test_policy_state_is_separate_from_provider_health() -> None:
    plugins = PluginRegistry()
    plugins.register(_CapabilityPlugin(risk=RiskLevel.MEDIUM))
    registry = CapabilityRegistry(plugins)

    missing_permission = registry.snapshot("discord.expression.send", _context(max_risk=RiskLevel.MEDIUM))
    assert missing_permission is not None
    assert missing_permission.availability == CapabilityAvailabilityState.FORBIDDEN
    assert "CAPABILITY_PERMISSION_REQUIRED" in missing_permission.reason_codes

    risk_limited = registry.snapshot(
        "discord.expression.send",
        _context(permissions={"discord:expression:send"}, max_risk=RiskLevel.LOW),
    )
    assert risk_limited is not None
    assert risk_limited.availability == CapabilityAvailabilityState.FORBIDDEN
    assert "CAPABILITY_RISK_LIMIT_EXCEEDED" in risk_limited.reason_codes


def test_remote_capability_requires_remote_processing_authority() -> None:
    plugins = PluginRegistry()
    plugins.register(_CapabilityPlugin(execution_scope=ExecutionScope.REMOTE))
    registry = CapabilityRegistry(plugins)

    forbidden = registry.snapshot(
        "discord.expression.send",
        _context(permissions={"discord:expression:send"}, remote=False),
    )
    assert forbidden is not None
    assert forbidden.availability == CapabilityAvailabilityState.FORBIDDEN
    assert "CAPABILITY_REMOTE_PROCESSING_FORBIDDEN" in forbidden.reason_codes

    available = registry.snapshot(
        "discord.expression.send",
        _context(permissions={"discord:expression:send"}, remote=True),
    )
    assert available is not None
    assert available.availability == CapabilityAvailabilityState.AVAILABLE


def test_known_capability_can_exist_before_executor_is_available() -> None:
    plugins = PluginRegistry()
    registry = CapabilityRegistry(plugins)
    registry.register(
        CapabilityDeclaration(
            descriptor=CapabilityDescriptor(
                capability_id="minecraft.connect",
                semantic_action="enter_minecraft_world",
                summary="Connect Amora to a configured Minecraft world.",
                affordances={"minecraft", "connect_world"},
            ),
            binding=CapabilityBinding(plugin_id="minecraft.runtime", tool_name="connect"),
        )
    )

    snapshot = registry.snapshot("minecraft.connect")
    assert snapshot is not None
    assert snapshot.availability == CapabilityAvailabilityState.UNAVAILABLE
    assert "CAPABILITY_BINDING_UNAVAILABLE" in snapshot.reason_codes


def test_retriever_projects_natural_context_without_becoming_an_execution_gate() -> None:
    plugins = PluginRegistry()
    plugins.register(_CapabilityPlugin())
    registry = CapabilityRegistry(plugins)
    context = _context(permissions={"discord:expression:send"})

    matches = registry.retrieve(
        CapabilityQuery(text="iae amora, vem abrir esse sorrisão pro pai", top_k=4),
        context,
    )
    assert matches
    assert matches[0].snapshot.descriptor.capability_id == "discord.expression.send"
    assert "sorrisao" in matches[0].matched_terms

    unrelated = registry.retrieve(
        CapabilityQuery(text="qual é a previsão do tempo amanhã?", top_k=4),
        context,
    )
    assert unrelated == []


def test_duplicate_capability_ids_are_rejected_across_plugins() -> None:
    plugins = PluginRegistry()
    plugins.register(_CapabilityPlugin(plugin_id="discord.one"))
    plugins.register(_CapabilityPlugin(plugin_id="discord.two"))
    registry = CapabilityRegistry(plugins)

    try:
        _ = registry.capability_count
    except ValueError as exc:
        assert "duplicate capability id" in str(exc)
    else:
        raise AssertionError("duplicate capability id should fail closed")
