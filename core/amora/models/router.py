from __future__ import annotations

from dataclasses import dataclass, field, replace
from enum import StrEnum

from amora.events import PrivacyScope


class TaskClass(StrEnum):
    VISION = "vision"
    AUDIO = "audio"
    SOCIAL_DIALOGUE = "social_dialogue"
    REASONING = "reasoning"
    SUMMARIZATION = "summarization"
    CLASSIFICATION = "classification"
    TOOL_PLANNING = "tool_planning"
    TTS = "tts"


class ProviderClass(StrEnum):
    LOCAL = "local"
    REMOTE_EPHEMERAL = "remote_ephemeral"
    REMOTE_GENERAL = "remote_general"


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    name: str
    capabilities: frozenset[TaskClass]
    model: str | None = None
    quality: float = 0.5
    latency_score: float = 0.5
    cost_score: float = 0.0
    available: bool = True
    quota_remaining: float | None = None
    provider_class: ProviderClass = ProviderClass.REMOTE_GENERAL
    allowed_privacy_scopes: frozenset[PrivacyScope] = frozenset({PrivacyScope.PUBLIC, PrivacyScope.PERSONAL})
    supports_streaming: bool = False
    supports_structured_output: bool = False
    context_window_tokens: int | None = None
    priority: int = 0
    metadata: dict[str, object] = field(default_factory=dict)

    @property
    def remote(self) -> bool:
        return self.provider_class != ProviderClass.LOCAL


class ModelRouter:
    """Provider/model selector with privacy, quota and ranked failover gates.

    The router chooses compute. It never owns Amora's identity or canonical state.
    Existing V0.x callers can still call ``select(task)`` without extra arguments.
    """

    def __init__(self) -> None:
        self._providers: dict[str, ProviderDescriptor] = {}

    def register(self, provider: ProviderDescriptor) -> None:
        self._providers[provider.name] = provider

    def unregister(self, name: str) -> None:
        self._providers.pop(name, None)

    def set_availability(self, name: str, available: bool, *, quota_remaining: float | None = None) -> None:
        current = self._providers.get(name)
        if current is None:
            raise KeyError(name)
        self._providers[name] = replace(
            current,
            available=available,
            quota_remaining=current.quota_remaining if quota_remaining is None else quota_remaining,
        )

    def set_quota(self, name: str, quota_remaining: float | None) -> None:
        current = self._providers.get(name)
        if current is None:
            raise KeyError(name)
        self._providers[name] = replace(current, quota_remaining=quota_remaining)

    def ranked_candidates(
        self,
        task: TaskClass,
        *,
        privacy_scope: PrivacyScope = PrivacyScope.PERSONAL,
        remote_processing_allowed: bool = True,
        require_streaming: bool = False,
        require_structured_output: bool = False,
        estimated_input_tokens: int | None = None,
        excluded_names: set[str] | None = None,
    ) -> tuple[ProviderDescriptor, ...]:
        excluded = excluded_names or set()
        candidates: list[ProviderDescriptor] = []
        for provider in self._providers.values():
            if provider.name in excluded:
                continue
            if not provider.available or task not in provider.capabilities:
                continue
            if provider.quota_remaining is not None and provider.quota_remaining <= 0:
                continue
            if require_streaming and not provider.supports_streaming:
                continue
            if require_structured_output and not provider.supports_structured_output:
                continue
            if provider.remote:
                if not remote_processing_allowed:
                    continue
                if privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
                    continue
                if privacy_scope not in provider.allowed_privacy_scopes:
                    continue
            if estimated_input_tokens and provider.context_window_tokens and estimated_input_tokens > provider.context_window_tokens:
                continue
            candidates.append(provider)
        return tuple(sorted(candidates, key=self._score, reverse=True))

    def select(
        self,
        task: TaskClass,
        *,
        privacy_scope: PrivacyScope = PrivacyScope.PERSONAL,
        remote_processing_allowed: bool = True,
        require_streaming: bool = False,
        estimated_input_tokens: int | None = None,
    ) -> ProviderDescriptor | None:
        candidates = self.ranked_candidates(
            task,
            privacy_scope=privacy_scope,
            remote_processing_allowed=remote_processing_allowed,
            require_streaming=require_streaming,
            estimated_input_tokens=estimated_input_tokens,
        )
        return candidates[0] if candidates else None

    @staticmethod
    def _score(provider: ProviderDescriptor) -> float:
        privacy_bonus = 0.04 if provider.provider_class == ProviderClass.LOCAL else 0.0
        streaming_bonus = 0.04 if provider.supports_streaming else 0.0
        priority_bonus = max(-0.10, min(0.10, provider.priority * 0.01))
        quota_bonus = 0.0
        if provider.quota_remaining is not None:
            quota_bonus = 0.02 if provider.quota_remaining > 0 else -1.0
        return (
            provider.quality * 0.52
            + provider.latency_score * 0.24
            + (1.0 - provider.cost_score) * 0.20
            + privacy_bonus
            + streaming_bonus
            + priority_bonus
            + quota_bonus
        )

    def descriptor(self, name: str) -> ProviderDescriptor | None:
        return self._providers.get(name)

    def descriptors(self) -> tuple[ProviderDescriptor, ...]:
        return tuple(sorted(self._providers.values(), key=lambda item: item.name))

    @property
    def provider_count(self) -> int:
        return len(self._providers)
