from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .models import CognitiveContext, ContextCapability


_EXECUTABLE_AVAILABILITY = {"available", "degraded"}
_TRANSPORT_PRECONDITION_PREFIX = "current_transport_is_"


@dataclass(slots=True)
class CapabilityIntentDecision:
    matched: bool = False
    capability_id: str | None = None
    availability: str | None = None
    allowed_kind: bool = False
    decision: str = "not_capability_grounded"
    reason_codes: list[str] = field(default_factory=list)


class CapabilityIntentGate:
    """Ground a model semantic action in capabilities projected this turn.

    The model never chooses a native plugin/tool binding. It may only suggest a
    semantic ``action_kind``. This gate resolves that action against the exact
    provider-safe capability slice compiled for the request and re-checks local
    transport preconditions using trusted request metadata. Missing, ambiguous,
    unavailable, forbidden or wrong-transport capabilities never become
    executable authority.
    """

    @staticmethod
    def evaluate(
        action_kind: str,
        context: CognitiveContext,
        *,
        request_metadata: dict[str, Any] | None = None,
    ) -> CapabilityIntentDecision:
        normalized = action_kind.strip()
        matches = [
            item for item in context.capabilities
            if item.semantic_action == normalized
        ]
        if not matches:
            return CapabilityIntentDecision(
                matched=False,
                reason_codes=["CAPABILITY_ACTION_NOT_PROJECTED"],
            )
        if len(matches) != 1:
            return CapabilityIntentDecision(
                matched=True,
                allowed_kind=False,
                decision="rejected_by_local_gate",
                reason_codes=["CAPABILITY_ACTION_AMBIGUOUS"],
            )

        capability = matches[0]
        availability = capability.availability
        transport_reason = CapabilityIntentGate._transport_reason(
            capability,
            request_metadata=request_metadata or {},
        )
        if transport_reason is not None:
            return CapabilityIntentDecision(
                matched=True,
                capability_id=capability.capability_id,
                availability=availability,
                allowed_kind=False,
                decision="rejected_by_local_gate",
                reason_codes=[
                    "CAPABILITY_GROUNDED_TO_TURN",
                    transport_reason,
                    "MODEL_HAS_NO_EXECUTION_AUTHORITY",
                ],
            )

        if availability in _EXECUTABLE_AVAILABILITY:
            return CapabilityIntentDecision(
                matched=True,
                capability_id=capability.capability_id,
                availability=availability,
                allowed_kind=True,
                decision="deferred_to_bdi",
                reason_codes=[
                    "CAPABILITY_GROUNDED_TO_TURN",
                    f"CAPABILITY_{availability.upper()}",
                    "MODEL_HAS_NO_EXECUTION_AUTHORITY",
                ],
            )
        if availability == "unavailable":
            return CapabilityIntentDecision(
                matched=True,
                capability_id=capability.capability_id,
                availability=availability,
                allowed_kind=False,
                decision="deferred_capability_unavailable",
                reason_codes=[
                    "CAPABILITY_GROUNDED_TO_TURN",
                    "CAPABILITY_UNAVAILABLE",
                    "MODEL_HAS_NO_EXECUTION_AUTHORITY",
                ],
            )
        if availability == "forbidden":
            return CapabilityIntentDecision(
                matched=True,
                capability_id=capability.capability_id,
                availability=availability,
                allowed_kind=False,
                decision="rejected_by_local_gate",
                reason_codes=[
                    "CAPABILITY_GROUNDED_TO_TURN",
                    "CAPABILITY_FORBIDDEN",
                    "MODEL_HAS_NO_EXECUTION_AUTHORITY",
                ],
            )
        return CapabilityIntentDecision(
            matched=True,
            capability_id=capability.capability_id,
            availability=availability,
            allowed_kind=False,
            decision="rejected_by_local_gate",
            reason_codes=[
                "CAPABILITY_GROUNDED_TO_TURN",
                "CAPABILITY_AVAILABILITY_UNKNOWN",
                "MODEL_HAS_NO_EXECUTION_AUTHORITY",
            ],
        )

    @staticmethod
    def _transport_reason(
        capability: ContextCapability,
        *,
        request_metadata: dict[str, Any],
    ) -> str | None:
        required = [
            item[len(_TRANSPORT_PRECONDITION_PREFIX):]
            for item in capability.preconditions
            if item.startswith(_TRANSPORT_PRECONDITION_PREFIX)
        ]
        if not required:
            return None
        platform = str(request_metadata.get("platform") or "").strip().lower()
        if not platform:
            return "CAPABILITY_TRANSPORT_CONTEXT_MISSING"
        if platform not in {item.strip().lower() for item in required}:
            return "CAPABILITY_TRANSPORT_MISMATCH"
        return None

    @staticmethod
    def projected_for_action(action_kind: str, context: CognitiveContext) -> list[ContextCapability]:
        return [item for item in context.capabilities if item.semantic_action == action_kind.strip()]
