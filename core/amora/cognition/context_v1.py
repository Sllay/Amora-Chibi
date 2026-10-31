from __future__ import annotations

from time import perf_counter

from amora.latency_trace import set_ms

from .context import ContextCompiler as BaseContextCompiler
from .models import CognitiveContext, CognitiveRequest


class ContextCompiler(BaseContextCompiler):
    """V1 context compiler extensions that preserve the canonical base compiler.

    Cognitive mode is semantic input to the Dialogue Orchestrator. The base
    compiler builds the canonical snapshot first; V1 then applies mode reason
    codes and recomputes the plan so autonomous/reflection turns can never carry
    a stale ordinary-conversation DialoguePlan.
    """

    def compile(self, request: CognitiveRequest, *, token_budget: int = 3200) -> CognitiveContext:
        started = perf_counter()
        try:
            context = super().compile(request, token_budget=token_budget)
            mode = str(request.metadata.get("cognitive_mode") or "reply")
            mode_code: str | None = None
            if mode == "autonomous_initiation":
                mode_code = "AUTONOMOUS_INITIATION"
                # In this mode request.text is an internal objective, not an
                # observed utterance. Do not mislabel it as received-speech ACIR.
                context.acir = None
                context.reason_codes = [
                    code
                    for code in context.reason_codes
                    if code not in {"ACIR_COMPILED", "ACIR_BOUND_TO_DIALOGUE_PLAN"}
                ]
                context.reason_codes.append("ACIR_NOT_APPLICABLE_AUTONOMOUS_INITIATION")
            elif mode == "reflection":
                mode_code = "COGNITIVE_REFLECTION"

            if mode_code is None:
                return context

            old_plan_codes = {str(code) for code in context.dialogue_plan.get("reason_codes") or []}
            context.reason_codes = [
                code
                for code in context.reason_codes
                if code not in old_plan_codes and not code.startswith("DIALOGUE_ACT_")
            ]
            if mode_code not in context.reason_codes:
                context.reason_codes.append(mode_code)

            dialogue_plan = self.dialogue.plan(context)
            context.dialogue_plan = dialogue_plan.model_dump(mode="json")
            if context.acir is not None:
                context.dialogue_plan["input_acir"] = context.acir
                if "ACIR_BOUND_TO_DIALOGUE_PLAN" not in context.reason_codes:
                    context.reason_codes.append("ACIR_BOUND_TO_DIALOGUE_PLAN")
            context.reason_codes.extend(
                code for code in dialogue_plan.reason_codes if code not in context.reason_codes
            )
            context.reason_codes.append(f"DIALOGUE_ACT_{dialogue_plan.act.value.upper()}")
            return self._fit_budget(context)
        finally:
            set_ms("context_compile_ms", (perf_counter() - started) * 1000.0)
