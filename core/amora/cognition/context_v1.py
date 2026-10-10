from __future__ import annotations

from .context import ContextCompiler as BaseContextCompiler
from .models import CognitiveContext, CognitiveRequest


class ContextCompiler(BaseContextCompiler):
    """V1 context compiler extensions that preserve the canonical base compiler."""

    def compile(self, request: CognitiveRequest, *, token_budget: int = 3200) -> CognitiveContext:
        context = super().compile(request, token_budget=token_budget)
        mode = str(request.metadata.get("cognitive_mode") or "reply")
        if mode == "autonomous_initiation":
            if "AUTONOMOUS_INITIATION" not in context.reason_codes:
                context.reason_codes.append("AUTONOMOUS_INITIATION")
        elif mode == "reflection":
            if "COGNITIVE_REFLECTION" not in context.reason_codes:
                context.reason_codes.append("COGNITIVE_REFLECTION")
        return context
