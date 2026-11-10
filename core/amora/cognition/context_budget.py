"""Conservative character-based envelope estimates, explicitly not tokenizer counts."""
import json
import math


class ContextBudgetExceeded(ValueError):
    code = 'CONTEXT_ENVELOPE_OVERFLOW'
    def __init__(self, estimated, budget):
        self.estimated = estimated
        self.budget = budget
        super().__init__(self.code)


def estimate_payload(payload):
    return math.ceil(len(json.dumps(payload, ensure_ascii=False, separators=(',', ':'))) / 4)


def ensure_payload(context, payload):
    estimated = estimate_payload(payload)
    if estimated > context.token_budget:
        raise ContextBudgetExceeded(estimated, context.token_budget)
    return estimated


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
GROQ_PAYLOAD_HARD_CEILING_TOKENS = 7_200


def fit_provider_payload(context, make_payload, *, ceiling=GROQ_PAYLOAD_SOFT_CEILING_TOKENS):
    """Return a copy with optional context projected to a provider-specific cap.

    The original Core context and DialoguePlan remain authoritative and intact.
    The character/4 estimate is conservative guidance, not a real tokenizer
    or a substitute for provider response/error diagnostics.
    """
    target = min(int(context.token_budget), int(ceiling))
    if target <= 0:
        raise ContextBudgetExceeded(1, target)
    projected = context.model_copy(deep=True)
    while True:
        payload = make_payload(projected)
        estimate = estimate_payload(payload)
        if estimate <= target:
            return projected, payload

        # Optional, lowest-authority projection data first. Never rewrite the
        # user utterance, plan, speaker/addressee, canonical identity or V3.
        if projected.specialist_support is not None:
            projected.specialist_support = None
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
            hard = min(int(context.token_budget), GROQ_PAYLOAD_HARD_CEILING_TOKENS)
            if estimate <= hard:
                return projected, payload
            raise ContextBudgetExceeded(estimate, hard)
