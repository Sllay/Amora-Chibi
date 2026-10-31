from __future__ import annotations

from .providers_identity import GeminiCognitiveProvider
from .structured_runtime import CognitiveEngine as BaseCognitiveEngine


class CognitiveEngine(BaseCognitiveEngine):
    """Structured runtime whose automatically-registered Gemini uses ProviderFrame v1."""

    def register_provider(self, provider) -> None:
        super().register_provider(provider)
        if provider.descriptor.name != "groq":
            return
        secrets = getattr(provider, "secrets", None)
        if secrets is None:
            return
        identity_gemini = GeminiCognitiveProvider(secrets)
        if not identity_gemini.descriptor.available:
            return
        # Base structured runtime may already have inserted a transport-compatible
        # Gemini fallback. Replace it by name; ModelRouter.register is an upsert.
        self._providers["gemini"] = identity_gemini
        self.router.register(identity_gemini.descriptor)
