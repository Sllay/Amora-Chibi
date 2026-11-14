"""Separate Groq source-only reflection from the conversational provider."""
from dataclasses import replace

from amora.models import TaskClass
from .providers import ProviderError
from .providers_identity import GroqCognitiveProvider
from .reflection_contract import is_memory_only


class GroqMemoryProvider:
    def __init__(self, secrets, *, model: str, name: str = "groq_memory", primary: bool = True):
        self._transport = GroqCognitiveProvider(secrets, model=model)
        self.descriptor = replace(
            self._transport.descriptor, name=name,
            capabilities=frozenset({TaskClass.REASONING}),
            supports_streaming=False,
            metadata={"memory_reflection_only": True, "memory_primary_preference": primary},
        )

    async def propose(self, context):
        if not is_memory_only(context):
            raise ProviderError("Groq memory specialist requires source-only reflection")
        return await self._transport.propose(context)
