from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class TaskClass(StrEnum):
    VISION = "vision"
    AUDIO = "audio"
    SOCIAL_DIALOGUE = "social_dialogue"
    REASONING = "reasoning"
    SUMMARIZATION = "summarization"
    CLASSIFICATION = "classification"
    TOOL_PLANNING = "tool_planning"


@dataclass(frozen=True, slots=True)
class ProviderDescriptor:
    name: str
    capabilities: frozenset[TaskClass]
    quality: float = 0.5
    latency_score: float = 0.5
    cost_score: float = 0.0
    available: bool = True
    quota_remaining: float | None = None
    metadata: dict[str, object] = field(default_factory=dict)


class ModelRouter:
    def __init__(self) -> None:
        self._providers: dict[str, ProviderDescriptor] = {}

    def register(self, provider: ProviderDescriptor) -> None:
        self._providers[provider.name] = provider

    def select(self, task: TaskClass) -> ProviderDescriptor | None:
        candidates = [p for p in self._providers.values() if p.available and task in p.capabilities and (p.quota_remaining is None or p.quota_remaining > 0)]
        if not candidates:
            return None
        def score(provider: ProviderDescriptor) -> float:
            return provider.quality * 0.55 + provider.latency_score * 0.25 + (1.0 - provider.cost_score) * 0.20
        return max(candidates, key=score)

    @property
    def provider_count(self) -> int:
        return len(self._providers)
