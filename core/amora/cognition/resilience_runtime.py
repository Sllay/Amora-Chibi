from __future__ import annotations

from .identity_runtime import CognitiveEngine as IdentityAwareCognitiveEngine
from .provider_resilience import wrap_resilient_provider


class CognitiveEngine(IdentityAwareCognitiveEngine):
    """Identity-aware cognition with bounded transient provider recovery."""

    def register_provider(self, provider) -> None:
        # Let the identity runtime register/replace all canonical provider
        # adapters first (including automatic Gemini failover), then decorate the
        # final structured adapters in place. Router descriptors stay unchanged.
        super().register_provider(provider)
        for name, current in list(self._providers.items()):
            wrapped = wrap_resilient_provider(current)
            if wrapped is not current:
                self._providers[name] = wrapped
