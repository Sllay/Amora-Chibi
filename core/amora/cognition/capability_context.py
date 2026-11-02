from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from amora.actions.capabilities import CapabilityQuery
from amora.actions.runtime_capabilities import resolve_runtime_capabilities

from .models import CognitiveContext, CognitiveRequest, ContextCapability

if TYPE_CHECKING:
    from amora.actions.capabilities import CapabilityRegistry


@dataclass(slots=True)
class CapabilityProjectionResult:
    capabilities: list[ContextCapability] = field(default_factory=list)
    reason_codes: list[str] = field(default_factory=list)
    selected_capability_ids: list[str] = field(default_factory=list)
    estimated_tokens: int = 0
    truncated: bool = False


class CapabilityContextProjector:
    """Compile a small provider-safe capability slice for one cognitive turn.

    Retrieval answers only "which affordances are worth mentioning right now?".
    It never grants execution permission and deliberately resolves the registry
    without a per-turn ExecutionContext. Actual execution remains exclusively
    guarded by ActionBus policy/risk/permission/effect verification.

    A transport may additionally declare a capability available only for the
    current response surface (for example, a Discord sticker catalog that was
    actually fetched for this guild). That contextual availability changes only
    the provider-safe projection for this request; the persistent registry is not
    mutated and no native asset id is exposed to cognition.
    """

    MAX_ITEMS = 6
    MAX_CAPABILITY_TOKENS = 700
    MIN_CAPABILITY_TOKENS = 160

    def __init__(
        self,
        runtime_owner: object,
        *,
        max_items: int = MAX_ITEMS,
        token_budget: int = MAX_CAPABILITY_TOKENS,
    ) -> None:
        self.runtime_owner = runtime_owner
        self.max_items = max(1, min(16, int(max_items)))
        self.token_budget = max(self.MIN_CAPABILITY_TOKENS, min(1600, int(token_budget)))

    def project(self, request: CognitiveRequest, context: CognitiveContext) -> CapabilityProjectionResult:
        registry = self._registry()
        if registry is None:
            return CapabilityProjectionResult(reason_codes=["CAPABILITY_REGISTRY_NOT_BOUND"])

        active_ids = self._string_set(request.metadata.get("active_capability_ids"))
        surface_available = self._string_set(request.metadata.get("surface_available_capability_ids"))
        query = CapabilityQuery(
            text=request.text,
            top_k=min(32, self.max_items * 3),
            include_unavailable=True,
            include_forbidden=True,
            active_capability_ids=active_ids,
        )
        matches = registry.retrieve(query, context=None)
        if not matches:
            return CapabilityProjectionResult(reason_codes=["CAPABILITY_CONTEXT_EMPTY"])

        effective_budget = min(
            self.token_budget,
            max(self.MIN_CAPABILITY_TOKENS, int(context.token_budget) // 4),
        )
        projected: list[ContextCapability] = []
        selected: list[str] = []
        used_tokens = 0
        truncated = False
        contextual_availability_used = False

        for match in matches:
            if len(projected) >= self.max_items:
                truncated = True
                break

            raw = match.snapshot.cognitive_projection()
            capability_id = str(raw["capability_id"])
            availability = str(raw["availability"])
            if capability_id in surface_available:
                availability = "available"
                contextual_availability_used = True
            item = ContextCapability(
                capability_id=capability_id,
                semantic_action=str(raw["semantic_action"]),
                summary=str(raw["summary"])[:700],
                affordances=[str(value) for value in raw.get("affordances", [])[:8]],
                availability=availability,
                inputs=dict(raw.get("inputs") or {}),
                outputs=dict(raw.get("outputs") or {}),
                preconditions=[str(value) for value in raw.get("preconditions", [])[:12]],
                effects=[str(value) for value in raw.get("effects", [])[:12]],
                policy=dict(raw.get("policy") or {}),
                verification_effect=(
                    str(raw["verification_effect"])
                    if raw.get("verification_effect") is not None
                    else None
                ),
            )
            estimated = self._estimate_tokens(item)
            if projected and used_tokens + estimated > effective_budget:
                truncated = True
                break
            if not projected and estimated > effective_budget:
                item = self._compact(item)
                estimated = self._estimate_tokens(item)
                if estimated > effective_budget:
                    truncated = True
                    continue

            projected.append(item)
            selected.append(item.capability_id)
            used_tokens += estimated

        reasons = ["CAPABILITY_CONTEXT_PROJECTED", "CAPABILITY_CONTEXT_NON_AUTHORITATIVE"] if projected else [
            "CAPABILITY_CONTEXT_EMPTY"
        ]
        if contextual_availability_used:
            reasons.append("CAPABILITY_SURFACE_AVAILABILITY_PROJECTED")
        if active_ids:
            reasons.append("CAPABILITY_SURFACE_ACTIVE_IDS_APPLIED")
        if truncated:
            reasons.append("CAPABILITY_CONTEXT_TRIMMED")
        return CapabilityProjectionResult(
            capabilities=projected,
            reason_codes=reasons,
            selected_capability_ids=selected,
            estimated_tokens=used_tokens,
            truncated=truncated,
        )

    def _registry(self) -> CapabilityRegistry | None:
        return resolve_runtime_capabilities(self.runtime_owner)

    @staticmethod
    def _string_set(value: object) -> set[str]:
        if not isinstance(value, (list, tuple, set, frozenset)):
            return set()
        return {str(item).strip() for item in value if str(item).strip()}

    @staticmethod
    def _estimate_tokens(item: ContextCapability) -> int:
        encoded = json.dumps(item.model_dump(mode="json"), ensure_ascii=False, separators=(",", ":"))
        return max(1, len(encoded) // 4)

    @staticmethod
    def _compact(item: ContextCapability) -> ContextCapability:
        """Keep the semantic minimum if one descriptor is unusually verbose."""

        return item.model_copy(
            update={
                "summary": item.summary[:280],
                "affordances": item.affordances[:4],
                "inputs": {},
                "outputs": {},
                "preconditions": item.preconditions[:4],
                "effects": item.effects[:4],
            }
        )
