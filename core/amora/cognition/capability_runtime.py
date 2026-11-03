from __future__ import annotations

from amora.events import AmoraEvent

from .capability_intent import CapabilityIntentGate
from .resilience_runtime import CognitiveEngine as ResilientCognitiveEngine


class CognitiveEngine(ResilientCognitiveEngine):
    """Cognition layer that grounds semantic actions in turn capabilities.

    The provider can suggest only a semantic ``action_kind``. After the normal
    reflection pipeline returns, the Core recompiles the local context for the
    same request and resolves that semantic action against capabilities actually
    projected for this turn. A grounded suggestion is still only evidence for
    BDI; this layer never resolves plugin/tool bindings or dispatches ActionBus.
    """

    async def reflect(self, request):
        bundle = await super().reflect(request)
        suggestions = list(bundle.action_suggestions)
        if not suggestions:
            return bundle

        context = self.compiler.compile(request)
        for index, suggestion in enumerate(suggestions):
            gate = CapabilityIntentGate.evaluate(
                suggestion.action_kind,
                context,
                request_metadata=request.metadata,
            )
            if not gate.matched:
                continue

            target_id = suggestion.target_id
            target_known = target_id is None or self.identities.get(target_id) is not None
            allowed = gate.allowed_kind and target_known
            decision = gate.decision if target_known else "rejected_by_local_gate"
            reason_codes = list(gate.reason_codes)
            if not target_known:
                reason_codes.append("CAPABILITY_TARGET_UNKNOWN")

            await self.events.publish(
                AmoraEvent(
                    event_type="cognition.action_suggested",
                    timestamp=self.clock.now(),
                    source="cognitive_engine:capability_grounding",
                    actor="ENTITY_AMORA",
                    target=target_id,
                    confidence=suggestion.confidence,
                    importance=suggestion.urgency,
                    payload={
                        "turn_id": str(request.turn_id),
                        "action_kind": suggestion.action_kind,
                        "capability_id": gate.capability_id,
                        "capability_availability": gate.availability,
                        "capability_grounded": True,
                        "allowed_kind": allowed,
                        "provider_reason_code": suggestion.reason_code,
                        "reason_code": reason_codes[0] if reason_codes else "CAPABILITY_GROUNDED",
                        "reason_codes": reason_codes,
                        "decision": decision,
                        "model_has_execution_authority": False,
                    },
                    privacy_scope=request.privacy_scope,
                    dedupe_key=(
                        f"cognition.capability_grounded:{request.turn_id}:"
                        f"{gate.capability_id or suggestion.action_kind}:{index}"
                    ),
                )
            )

        return bundle
