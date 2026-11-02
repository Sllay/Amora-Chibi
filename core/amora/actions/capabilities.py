from __future__ import annotations

import re
import unicodedata
from enum import StrEnum
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from .models import ExecutionContext, ExecutionScope, PluginHealth, RiskLevel

if TYPE_CHECKING:
    from .plugins import PluginRegistry


class CapabilityKnowledgeState(StrEnum):
    KNOWN = "known"


class CapabilityAvailabilityState(StrEnum):
    AVAILABLE = "available"
    DEGRADED = "degraded"
    UNAVAILABLE = "unavailable"
    FORBIDDEN = "forbidden"


class CapabilityConsent(StrEnum):
    NONE = "none"
    IMPLICIT = "implicit"
    EXPLICIT = "explicit"


class CapabilityDescriptor(BaseModel):
    """Semantic, provider-safe description of an actionable affordance.

    This model intentionally contains no native transport ids, plugin object
    references, credentials, endpoints or other execution authority. Physical
    execution lives in CapabilityBinding and stays local to the Core.
    """

    capability_id: str = Field(min_length=1, max_length=160)
    version: str = Field(default="1", min_length=1, max_length=64)
    semantic_action: str = Field(min_length=1, max_length=160)
    summary: str = Field(min_length=1, max_length=1000)
    affordances: set[str] = Field(default_factory=set)
    examples: list[str] = Field(default_factory=list, max_length=12)
    inputs: dict[str, Any] = Field(default_factory=dict)
    outputs: dict[str, Any] = Field(default_factory=dict)
    preconditions: list[str] = Field(default_factory=list, max_length=32)
    effects: list[str] = Field(default_factory=list, max_length=32)
    permissions: set[str] = Field(default_factory=set)
    risk: RiskLevel = RiskLevel.LOW
    cost: float = Field(default=0.0, ge=0.0, le=1.0)
    execution_scope: ExecutionScope = ExecutionScope.LOCAL
    idempotent: bool = True
    may_refuse: bool = False
    may_initiate: bool = False
    consent: CapabilityConsent = CapabilityConsent.NONE
    external_side_effect: bool = False
    verification_effect: str | None = Field(default=None, max_length=240)
    metadata: dict[str, Any] = Field(default_factory=dict)


class CapabilityBinding(BaseModel):
    """Local execution binding. Never belongs in provider-facing context."""

    plugin_id: str = Field(min_length=1, max_length=120)
    tool_name: str = Field(min_length=1, max_length=120)


class CapabilityDeclaration(BaseModel):
    descriptor: CapabilityDescriptor
    binding: CapabilityBinding


@runtime_checkable
class CapabilityProvider(Protocol):
    @property
    def capabilities(self) -> list[CapabilityDeclaration]: ...


class CapabilitySnapshot(BaseModel):
    descriptor: CapabilityDescriptor
    knowledge: CapabilityKnowledgeState = CapabilityKnowledgeState.KNOWN
    availability: CapabilityAvailabilityState
    reason_codes: list[str] = Field(default_factory=list)

    def cognitive_projection(self) -> dict[str, Any]:
        """Return only semantic affordances safe for cognitive projection.

        Binding ids, native platform ids, raw permission names and executable
        implementation details remain local. The cognitive layer only learns
        what the capability can do and whether it is currently usable.
        """

        descriptor = self.descriptor
        return {
            "capability_id": descriptor.capability_id,
            "version": descriptor.version,
            "semantic_action": descriptor.semantic_action,
            "summary": descriptor.summary,
            "affordances": sorted(descriptor.affordances),
            "examples": list(descriptor.examples),
            "inputs": descriptor.inputs,
            "outputs": descriptor.outputs,
            "preconditions": list(descriptor.preconditions),
            "effects": list(descriptor.effects),
            "availability": self.availability.value,
            "reason_codes": list(self.reason_codes),
            "policy": {
                "risk": descriptor.risk.name.lower(),
                "cost": descriptor.cost,
                "may_refuse": descriptor.may_refuse,
                "may_initiate": descriptor.may_initiate,
                "consent": descriptor.consent.value,
                "external_side_effect": descriptor.external_side_effect,
                "idempotent": descriptor.idempotent,
            },
            "verification_effect": descriptor.verification_effect,
        }


class CapabilityQuery(BaseModel):
    text: str = ""
    desired_affordances: set[str] = Field(default_factory=set)
    active_capability_ids: set[str] = Field(default_factory=set)
    top_k: int = Field(default=8, ge=1, le=32)
    include_unavailable: bool = True
    include_forbidden: bool = True


class CapabilityMatch(BaseModel):
    snapshot: CapabilitySnapshot
    score: float = Field(ge=0.0)
    matched_terms: list[str] = Field(default_factory=list)


class CapabilityRegistry:
    """Local source of truth for capabilities the Amora can reason about.

    Plugins may opt in by exposing a ``capabilities`` property containing
    CapabilityDeclaration objects. Existing/internal tools stay hidden unless
    they deliberately publish a semantic affordance. Manual declarations are
    also supported for adapters that are registered before their executor is
    connected; such capabilities remain KNOWN but UNAVAILABLE until binding.

    The registry is deliberately live: plugin health and newly registered
    plugins are read on every snapshot/retrieval, so a connector can transition
    from unavailable -> available without rebuilding cognitive state.
    """

    def __init__(self, plugins: PluginRegistry) -> None:
        self.plugins = plugins
        self._manual: dict[str, CapabilityDeclaration] = {}

    def register(self, declaration: CapabilityDeclaration) -> None:
        capability_id = declaration.descriptor.capability_id
        if capability_id in self._manual:
            raise ValueError(f"capability already registered: {capability_id}")
        self._manual[capability_id] = declaration
        # Validate against dynamic declarations immediately so duplicates are
        # deterministic and discovered at registration time when possible.
        try:
            self._collect_declarations()
        except Exception:
            self._manual.pop(capability_id, None)
            raise

    def unregister(self, capability_id: str) -> None:
        self._manual.pop(capability_id, None)

    def declaration(self, capability_id: str) -> CapabilityDeclaration | None:
        return self._collect_declarations().get(capability_id)

    def binding(self, capability_id: str) -> CapabilityBinding | None:
        declaration = self.declaration(capability_id)
        return declaration.binding if declaration is not None else None

    def list_known(self, context: ExecutionContext | None = None) -> list[CapabilitySnapshot]:
        declarations = self._collect_declarations()
        return [
            self._snapshot(declarations[capability_id], context)
            for capability_id in sorted(declarations)
        ]

    def snapshot(self, capability_id: str, context: ExecutionContext | None = None) -> CapabilitySnapshot | None:
        declaration = self.declaration(capability_id)
        if declaration is None:
            return None
        return self._snapshot(declaration, context)

    def retrieve(
        self,
        query: CapabilityQuery,
        context: ExecutionContext | None = None,
    ) -> list[CapabilityMatch]:
        """Return a small relevant subset without granting execution authority.

        This first implementation is a deterministic local lexical/affordance
        retriever. It is intentionally a *retrieval fallback*, not a keyword
        gate: matching only decides which known affordances are projected for
        reasoning. Permission/risk/health enforcement remains independent and
        execution still goes through ActionBus.
        """

        query_text = self._normalize(query.text)
        text_tokens = set(self._tokens(query_text))
        desired = {self._normalize(item) for item in query.desired_affordances if item}
        active = set(query.active_capability_ids)
        if not text_tokens and not desired and not active:
            return []

        matches: list[CapabilityMatch] = []
        for snapshot in self.list_known(context):
            if snapshot.availability == CapabilityAvailabilityState.UNAVAILABLE and not query.include_unavailable:
                continue
            if snapshot.availability == CapabilityAvailabilityState.FORBIDDEN and not query.include_forbidden:
                continue

            descriptor = snapshot.descriptor
            semantic_action = self._normalize(descriptor.semantic_action)
            affordances = {self._normalize(item) for item in descriptor.affordances}
            summary_tokens = set(self._tokens(self._normalize(descriptor.summary)))
            example_tokens = set()
            for example in descriptor.examples:
                example_tokens.update(self._tokens(self._normalize(example)))
            precondition_tokens = set()
            for item in descriptor.preconditions:
                precondition_tokens.update(self._tokens(self._normalize(item)))
            effect_tokens = set()
            for item in descriptor.effects:
                effect_tokens.update(self._tokens(self._normalize(item)))

            semantic_tokens = set(self._tokens(semantic_action))
            affordance_tokens = set()
            for item in affordances:
                affordance_tokens.update(self._tokens(item))

            matched_terms: set[str] = set()
            score = 0.0

            direct_affordances = desired.intersection(affordances | {semantic_action})
            if direct_affordances:
                score += 12.0 * len(direct_affordances)
                matched_terms.update(direct_affordances)

            semantic_overlap = text_tokens.intersection(semantic_tokens)
            if semantic_overlap:
                score += 7.0 * len(semantic_overlap)
                matched_terms.update(semantic_overlap)

            affordance_overlap = text_tokens.intersection(affordance_tokens)
            if affordance_overlap:
                score += 6.0 * len(affordance_overlap)
                matched_terms.update(affordance_overlap)

            example_overlap = text_tokens.intersection(example_tokens)
            if example_overlap:
                score += 3.5 * len(example_overlap)
                matched_terms.update(example_overlap)

            summary_overlap = text_tokens.intersection(summary_tokens)
            if summary_overlap:
                score += 2.0 * len(summary_overlap)
                matched_terms.update(summary_overlap)

            effect_overlap = text_tokens.intersection(effect_tokens)
            if effect_overlap:
                score += 1.5 * len(effect_overlap)
                matched_terms.update(effect_overlap)

            precondition_overlap = text_tokens.intersection(precondition_tokens)
            if precondition_overlap:
                score += 0.5 * len(precondition_overlap)
                matched_terms.update(precondition_overlap)

            if descriptor.capability_id in active:
                score += 4.0
                matched_terms.add(descriptor.capability_id)

            # Availability influences ordering but never authorizes execution.
            if snapshot.availability == CapabilityAvailabilityState.AVAILABLE:
                score += 1.0
            elif snapshot.availability == CapabilityAvailabilityState.DEGRADED:
                score += 0.25

            if score <= 0.0:
                continue
            matches.append(
                CapabilityMatch(
                    snapshot=snapshot,
                    score=score,
                    matched_terms=sorted(matched_terms),
                )
            )

        matches.sort(key=lambda item: (-item.score, item.snapshot.descriptor.capability_id))
        return matches[: query.top_k]

    @property
    def capability_count(self) -> int:
        return len(self._collect_declarations())

    def _collect_declarations(self) -> dict[str, CapabilityDeclaration]:
        collected: dict[str, CapabilityDeclaration] = dict(self._manual)
        origins: dict[str, str] = {key: "manual" for key in self._manual}

        for manifest in self.plugins.manifests():
            plugin = self.plugins.plugin(manifest.plugin_id)
            if plugin is None:
                continue
            declared = getattr(plugin, "capabilities", None)
            if callable(declared):
                declared = declared()
            if declared is None:
                continue
            for raw in declared:
                declaration = raw if isinstance(raw, CapabilityDeclaration) else CapabilityDeclaration.model_validate(raw)
                capability_id = declaration.descriptor.capability_id
                if declaration.binding.plugin_id != manifest.plugin_id:
                    raise ValueError(
                        f"capability binding plugin mismatch: {capability_id} -> "
                        f"{declaration.binding.plugin_id}, provider={manifest.plugin_id}"
                    )
                if capability_id in collected:
                    raise ValueError(
                        f"duplicate capability id: {capability_id} "
                        f"({origins[capability_id]} vs plugin:{manifest.plugin_id})"
                    )
                collected[capability_id] = declaration
                origins[capability_id] = f"plugin:{manifest.plugin_id}"
        return collected

    def _snapshot(
        self,
        declaration: CapabilityDeclaration,
        context: ExecutionContext | None,
    ) -> CapabilitySnapshot:
        descriptor, binding_reasons = self._effective_descriptor(declaration)
        reasons = list(binding_reasons)

        resolved = self.plugins.resolve_tool(declaration.binding.plugin_id, declaration.binding.tool_name)
        if resolved is None:
            if "CAPABILITY_BINDING_UNAVAILABLE" not in reasons:
                reasons.append("CAPABILITY_BINDING_UNAVAILABLE")
            return CapabilitySnapshot(
                descriptor=descriptor,
                availability=CapabilityAvailabilityState.UNAVAILABLE,
                reason_codes=reasons,
            )

        policy_reasons: list[str] = []
        if context is not None:
            missing_permissions = descriptor.permissions - context.granted_permissions
            if missing_permissions:
                policy_reasons.append("CAPABILITY_PERMISSION_REQUIRED")
            if descriptor.execution_scope == ExecutionScope.REMOTE and not context.remote_processing_allowed:
                policy_reasons.append("CAPABILITY_REMOTE_PROCESSING_FORBIDDEN")
            if descriptor.risk > context.max_risk:
                policy_reasons.append("CAPABILITY_RISK_LIMIT_EXCEEDED")
            if descriptor.cost > context.max_total_cost:
                policy_reasons.append("CAPABILITY_COST_LIMIT_EXCEEDED")

        if policy_reasons:
            return CapabilitySnapshot(
                descriptor=descriptor,
                availability=CapabilityAvailabilityState.FORBIDDEN,
                reason_codes=[*reasons, *policy_reasons],
            )

        health = self.plugins.health(declaration.binding.plugin_id)
        if health == PluginHealth.UNAVAILABLE:
            return CapabilitySnapshot(
                descriptor=descriptor,
                availability=CapabilityAvailabilityState.UNAVAILABLE,
                reason_codes=[*reasons, "CAPABILITY_PROVIDER_UNAVAILABLE"],
            )
        if health == PluginHealth.DEGRADED:
            return CapabilitySnapshot(
                descriptor=descriptor,
                availability=CapabilityAvailabilityState.DEGRADED,
                reason_codes=[*reasons, "CAPABILITY_PROVIDER_DEGRADED"],
            )
        return CapabilitySnapshot(
            descriptor=descriptor,
            availability=CapabilityAvailabilityState.AVAILABLE,
            reason_codes=reasons,
        )

    def _effective_descriptor(
        self,
        declaration: CapabilityDeclaration,
    ) -> tuple[CapabilityDescriptor, list[str]]:
        """Merge semantic declaration with stricter physical tool constraints."""

        descriptor = declaration.descriptor
        resolved = self.plugins.resolve_tool(declaration.binding.plugin_id, declaration.binding.tool_name)
        if resolved is None:
            return descriptor, ["CAPABILITY_BINDING_UNAVAILABLE"]
        plugin, tool = resolved
        manifest = plugin.manifest
        effective_permissions = descriptor.permissions | manifest.permissions | tool.permissions
        effective_risk = max(descriptor.risk, manifest.risk, tool.risk)
        physical_cost = min(1.0, manifest.cost + tool.cost)
        effective_cost = max(descriptor.cost, physical_cost)
        effective_scope = manifest.execution_scope
        effective_idempotent = descriptor.idempotent and tool.idempotent
        return (
            descriptor.model_copy(
                update={
                    "permissions": effective_permissions,
                    "risk": effective_risk,
                    "cost": effective_cost,
                    "execution_scope": effective_scope,
                    "idempotent": effective_idempotent,
                }
            ),
            [],
        )

    @staticmethod
    def _normalize(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.lower())
        ascii_value = "".join(char for char in decomposed if not unicodedata.combining(char))
        return " ".join(ascii_value.split())

    @staticmethod
    def _tokens(value: str) -> list[str]:
        return re.findall(r"[a-z0-9_]+", value.lower())
