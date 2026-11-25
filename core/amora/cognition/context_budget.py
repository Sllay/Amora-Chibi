"""Conservative character-based envelope estimates, explicitly not tokenizer counts."""
import json
import math
from dataclasses import dataclass


class ContextBudgetExceeded(ValueError):
    code = 'CONTEXT_ENVELOPE_OVERFLOW'
    def __init__(self, estimated, budget):
        self.estimated = estimated
        self.budget = budget
        super().__init__(self.code)


def estimate_payload(payload):
    return math.ceil(len(json.dumps(payload, ensure_ascii=False, separators=(',', ':'))) / 4)


def _positive_int(value):
    return value if type(value) is int and value > 0 else None


@dataclass(frozen=True, slots=True)
class EnvelopeBudget:
    estimated_input_tokens: int
    output_reserve_tokens: int
    body_bytes: int
    input_limit_tokens: int
    body_limit_bytes: int | None

    @property
    def fits(self):
        return (self.estimated_input_tokens <= self.input_limit_tokens
                and (self.body_limit_bytes is None or self.body_bytes <= self.body_limit_bytes))


def measure_envelope(context, payload, *, descriptor=None, ceiling=None):
    """Measure actual JSON bytes, reserve requested output and honor known limits.

    Local token budgets are input-only; provider context windows include output.
    Unknown provider limits remain unknown. This estimate is not a tokenizer.
    """
    reserve = next((value for key in ("max_output_tokens", "max_completion_tokens", "max_tokens")
                    if (value := _positive_int(payload.get(key))) is not None), 0)
    generation = payload.get("generationConfig")
    if not reserve and isinstance(generation, dict):
        reserve = _positive_int(generation.get("maxOutputTokens")) or 0
    limit = int(context.token_budget)
    if ceiling is not None:
        limit = min(limit, int(ceiling))
    window = _positive_int(getattr(descriptor, "context_window_tokens", None))
    if window is not None:
        limit = min(limit, window - reserve)
    metadata = getattr(descriptor, "metadata", {}) or {}
    byte_limit = _positive_int(metadata.get("max_request_bytes"))
    body_bytes = len(json.dumps(payload, ensure_ascii=False, separators=(",", ":"),
                                allow_nan=False).encode("utf-8"))
    return EnvelopeBudget(estimate_payload(payload), reserve, body_bytes, max(0, limit), byte_limit)


def ensure_payload(context, payload, *, descriptor=None):
    budget = measure_envelope(context, payload, descriptor=descriptor)
    if not budget.fits:
        raise ContextBudgetExceeded(budget.estimated_input_tokens, budget.input_limit_tokens)
    return budget.estimated_input_tokens


def estimate_context(context):
    from .prompt_frame import render_trusted_protocol, render_current_turn
    from .realization import realization_instruction
    from .providers_identity import _identity_history
    messages = [{'role': 'system', 'content': render_trusted_protocol(context) + '\n\n' + realization_instruction(context)}]
    messages.extend({'role': role, 'content': content} for role, content in _identity_history(context))
    messages.append({'role': 'user', 'content': render_current_turn(context)})
    return estimate_payload({'messages': messages}) + 128  # reserve transport fields


# Groq's hosted model has an account-specific token-per-minute budget, which can
# be much smaller than its advertised 131K-token context window. The base
# provider envelope is never shortened by cutting literal text, identity or the
# configured expressive profile. First discard only optional projections.
GROQ_PAYLOAD_SOFT_CEILING_TOKENS = 5_200
GROQ_PAYLOAD_HARD_CEILING_TOKENS = 16_384
GROQ_BASELINE_HARD_CEILING_TOKENS = 7_200
GROQ_V3_SOFT_CEILING_TOKENS = 12_288


def fit_provider_payload(context, make_payload, *, ceiling=GROQ_PAYLOAD_SOFT_CEILING_TOKENS, strict=False, descriptor=None):
    """Return a copy with optional context projected to a provider-specific cap.

    The original Core context and DialoguePlan remain authoritative and intact.
    The character/4 estimate is conservative guidance, not a real tokenizer
    or a substitute for provider response/error diagnostics.
    """
    full_v3 = context.expression_profile == 'amora-v3'
    # Preserve the default conservative baseline; the expanded V3 profile
    # requires more room for its immutable text and the authoritative plan.
    if not strict and full_v3 and ceiling == GROQ_PAYLOAD_SOFT_CEILING_TOKENS:
        ceiling = GROQ_V3_SOFT_CEILING_TOKENS
    target = min(int(context.token_budget), int(ceiling))
    if target <= 0:
        raise ContextBudgetExceeded(1, target)
    projected = context.model_copy(deep=True)
    while True:
        payload = make_payload(projected)
        budget = measure_envelope(projected, payload, descriptor=descriptor, ceiling=target)
        estimate = budget.estimated_input_tokens
        if budget.fits:
            return projected, payload

        # Optional, lowest-authority projection data first. Never rewrite the
        # user utterance, plan, speaker/addressee, canonical identity or V3.
        if projected.specialist_support is not None:
            projected.specialist_support = None
        elif projected.web_research and projected.web_research.get("research_notebook"):
            notebook = projected.web_research["research_notebook"]
            recent = notebook.get("recent_pages") or []
            saved = notebook.get("saved_links") or []
            if len(recent) > 3:
                recent.pop()
            elif len(saved) > 3:
                saved.pop()
            else:
                # Optional past web evidence yields before this turn's sources,
                # identity, memory or current user text under a tight Groq TPM.
                projected.web_research.pop("research_notebook", None)
        elif projected.web_research and len(projected.web_research.get("sources") or []) > 1:
            projected.web_research["sources"].pop()
        elif projected.web_research and (projected.web_research.get("sources") or []):
            # Keep the final current-turn source when shortening it is enough
            # to fit. The previous branch cleared it on the same iteration:
            # after [:600], len(text) <= 600 is always true.
            source = projected.web_research["sources"][0]
            text = source.get("text") or ""
            if len(text) > 600:
                source["text"] = text[:600]
            else:
                # Even a concise source exceeds the provider budget; discard
                # only after giving the shorter evidence a chance to fit.
                projected.web_research["sources"].clear()
        elif projected.capabilities:
            projected.capabilities.pop()
        elif len(projected.world_claims) > 2:
            projected.world_claims.pop()
        elif len(projected.memories) > 2:
            projected.memories.pop()
        elif len(projected.recent_messages) > 2:
            first = projected.recent_messages.pop(0)
            while (len(projected.recent_messages) > 2
                   and projected.recent_messages[0].get("turn_id") == first.get("turn_id")
                   and first.get("turn_id")):
                projected.recent_messages.pop(0)
        else:
            # A full V3 protocol plus the latest causal exchange can exceed
            # Groq's conservative *soft* target on its own. Prefer an explicit,
            # still-bounded hard ceiling to silently stripping canonical identity
            # or refusing every V3 turn before the provider is even called.
            hard_ceiling = (GROQ_PAYLOAD_HARD_CEILING_TOKENS if full_v3
                            else GROQ_BASELINE_HARD_CEILING_TOKENS)
            hard = target if strict else min(int(context.token_budget), hard_ceiling)
            hard_budget = measure_envelope(projected, payload, descriptor=descriptor, ceiling=hard)
            if hard_budget.fits:
                return projected, payload
            raise ContextBudgetExceeded(estimate, hard_budget.input_limit_tokens)


def compact_rejected_payload(context, make_payload, rejected_payload, *, descriptor=None):
    """One corrective projection for a rejected request, never the same bytes.

    Keep the original immutable turn, identity, plan and safety instructions.
    The 20% reduction target is a provisional policy, not a claimed provider
    limit. If essential context cannot fit, let the orchestrator try a different
    compatible route. No truncation of the literal user input.
    """
    previous = estimate_payload(rejected_payload)
    ceiling = max(1, int(previous * 0.8))
    def size(item):
        return len(json.dumps(item, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    # First seek the preferred reduction. If the irreducible causal exchange
    # prevents it, a strictly smaller projection is still useful. Neither
    # path may resend the rejected envelope or cut the protected context.
    for target in dict.fromkeys((ceiling, previous - 1)):
        if target < 1:
            continue
        try:
            _, payload = fit_provider_payload(context, make_payload, ceiling=target, strict=True, descriptor=descriptor)
        except ContextBudgetExceeded:
            continue
        if estimate_payload(payload) < previous and size(payload) < size(rejected_payload):
            return payload
    return None
