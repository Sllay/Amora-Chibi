from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import Iterator

from amora.events import PrivacyScope

from .router import ModelRouter, ProviderDescriptor, TaskClass


class ScopedModelRouter:
    """Delegating router with task-local provider pinning.

    The pin lives in a ContextVar, so one reflection attempt can force a specific
    provider without changing provider selection in concurrent conversations.
    """

    def __init__(self, base: ModelRouter) -> None:
        self.base = base
        self._pinned_provider: ContextVar[str | None] = ContextVar("amora_pinned_provider", default=None)

    @contextmanager
    def pin(self, provider_name: str) -> Iterator[None]:
        token = self._pinned_provider.set(provider_name)
        try:
            yield
        finally:
            self._pinned_provider.reset(token)

    def register(self, provider: ProviderDescriptor) -> None:
        self.base.register(provider)

    def unregister(self, name: str) -> None:
        self.base.unregister(name)

    def set_availability(self, name: str, available: bool, *, quota_remaining: float | None = None) -> None:
        self.base.set_availability(name, available, quota_remaining=quota_remaining)

    def set_quota(self, name: str, quota_remaining: float | None) -> None:
        self.base.set_quota(name, quota_remaining)

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
        return self.base.ranked_candidates(
            task,
            privacy_scope=privacy_scope,
            remote_processing_allowed=remote_processing_allowed,
            require_streaming=require_streaming,
            require_structured_output=require_structured_output,
            estimated_input_tokens=estimated_input_tokens,
            excluded_names=excluded_names,
        )

    def select(
        self,
        task: TaskClass,
        *,
        privacy_scope: PrivacyScope = PrivacyScope.PERSONAL,
        remote_processing_allowed: bool = True,
        require_streaming: bool = False,
        estimated_input_tokens: int | None = None,
    ) -> ProviderDescriptor | None:
        pinned = self._pinned_provider.get()
        if pinned is None:
            return self.base.select(
                task,
                privacy_scope=privacy_scope,
                remote_processing_allowed=remote_processing_allowed,
                require_streaming=require_streaming,
                estimated_input_tokens=estimated_input_tokens,
            )
        candidates = self.base.ranked_candidates(
            task,
            privacy_scope=privacy_scope,
            remote_processing_allowed=remote_processing_allowed,
            require_streaming=require_streaming,
            estimated_input_tokens=estimated_input_tokens,
        )
        return next((item for item in candidates if item.name == pinned), None)

    def descriptor(self, name: str) -> ProviderDescriptor | None:
        return self.base.descriptor(name)

    def descriptors(self) -> tuple[ProviderDescriptor, ...]:
        return self.base.descriptors()

    @property
    def provider_count(self) -> int:
        return self.base.provider_count
