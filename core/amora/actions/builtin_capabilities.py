from __future__ import annotations

from .capabilities import (
    CapabilityBinding,
    CapabilityConsent,
    CapabilityDeclaration,
    CapabilityDescriptor,
    CapabilityRegistry,
)
from .models import ExecutionScope, RiskLevel


DISCORD_EXPRESSION_CAPABILITY_ID = "discord.expression.send"


def discord_expression_capability() -> CapabilityDeclaration:
    """Describe Discord visual expression without pretending its executor exists.

    The binding is intentionally declarative. Until a ``discord.expression``
    plugin exposes ``send_expression``, CapabilityRegistry reports this affordance
    as KNOWN + UNAVAILABLE. Cognition may reason about it, but ActionBus cannot
    execute a missing binding.
    """

    return CapabilityDeclaration(
        descriptor=CapabilityDescriptor(
            capability_id=DISCORD_EXPRESSION_CAPABILITY_ID,
            semantic_action="express_visually",
            summary=(
                "Express a social reaction in the current Discord conversation "
                "using a transport-native visual expression such as a sticker."
            ),
            affordances={
                "visual_reaction",
                "social_expression",
                "sticker",
                "send_sticker",
                "express_visually",
            },
            examples=[
                "manda uma figurinha engraçada",
                "reage com uma figurinha",
                "abre esse sorrisão pro pai",
                "responde visualmente a isso",
            ],
            inputs={
                "expression_intent": {
                    "type": "string",
                    "description": "Semantic reaction intent resolved locally; never a native sticker id.",
                }
            },
            outputs={"receipt": "DeliveryReceipt"},
            preconditions=[
                "current_transport_is_discord",
                "target_conversation_resolved",
                "matching_visual_expression_available",
            ],
            effects=["visual_expression_delivered"],
            permissions={"discord:expression:send"},
            risk=RiskLevel.LOW,
            cost=0.02,
            execution_scope=ExecutionScope.LOCAL,
            idempotent=True,
            may_refuse=True,
            may_initiate=True,
            consent=CapabilityConsent.NONE,
            external_side_effect=True,
            verification_effect="message_delivered",
        ),
        binding=CapabilityBinding(
            plugin_id="discord.expression",
            tool_name="send_expression",
        ),
    )


def register_builtin_capabilities(registry: CapabilityRegistry) -> None:
    """Register canonical semantic affordances exactly once per Core registry."""

    if registry.declaration(DISCORD_EXPRESSION_CAPABILITY_ID) is None:
        registry.register(discord_expression_capability())
