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


def measure_envelope(context, payload, *, descriptor=None, ceiling=None, body_ceiling=None):
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
    if body_ceiling is not None:
        if type(body_ceiling) is not int or body_ceiling<1:
            raise ValueError("INVALID_BODY_CEILING")
        byte_limit = min(byte_limit,body_ceiling) if byte_limit is not None else body_ceiling
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


def fit_provider_payload(context, make_payload, *, ceiling=GROQ_PAYLOAD_SOFT_CEILING_TOKENS,
                         strict=False, descriptor=None, preserve_evidence=False, body_ceiling=None):
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
        budget = measure_envelope(projected, payload, descriptor=descriptor, ceiling=target,body_ceiling=body_ceiling)
        estimate = budget.estimated_input_tokens
        if budget.fits:
            return projected, payload

        # Optional, lowest-authority projection data first. Never rewrite the
        # user utterance, plan, speaker/addressee, canonical identity or V3.
        if projected.specialist_support is not None:
            projected.specialist_support = None
        elif preserve_evidence:
            # A rejected request must not be made to fit by dropping a source,
            # current fact, tool affordance or user constraint. Retain all
            # user turns and their associated historical replies for technical
            # continuation; otherwise only prune unambiguously unrelated
            # complete old exchanges. Never slice an utterance.
            from .task_contract import removable_history_turn
            removable=removable_history_turn(projected)
            if removable is not None:
                projected.recent_messages=[row for row in projected.recent_messages
                                           if str(row.get("turn_id") or "")!=removable]
            else:
                hard_ceiling=(GROQ_PAYLOAD_HARD_CEILING_TOKENS if full_v3
                              else GROQ_BASELINE_HARD_CEILING_TOKENS)
                hard=target if strict else min(int(context.token_budget),hard_ceiling)
                hard_budget=measure_envelope(projected,payload,descriptor=descriptor,ceiling=hard,body_ceiling=body_ceiling)
                if hard_budget.fits:
                    return projected,payload
                raise ContextBudgetExceeded(estimate,hard_budget.input_limit_tokens)
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
        elif (projected.web_research and projected.web_research.get("depth") == "deep"
              and projected.web_research.get("sources")):
            # Preserve competing evidence when possible: shorten each excerpt
            # before dropping an entire source, and label the lossy projection.
            research = projected.web_research
            sources = research["sources"]
            projection = research.setdefault("provider_evidence_projection", {
                "original_read_count": len(sources), "shortened_count": 0,
                "omitted_count": 0,
            })
            longest = max(sources, key=lambda item: len(item.get("text") or ""))
            if len(longest.get("text") or "") > 600:
                longest["text"] = longest["text"][:600]
                projection["shortened_count"] += 1
            else:
                sources.pop()
                projection["omitted_count"] += 1
                if not sources:
                    research["status"] = "results_only"
            projection["available_read_count"] = len(sources)
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
            hard_budget = measure_envelope(projected, payload, descriptor=descriptor, ceiling=hard,body_ceiling=body_ceiling)
            if hard_budget.fits:
                return projected, payload
            raise ContextBudgetExceeded(estimate, hard_budget.input_limit_tokens)


def compact_rejected_payload(context, make_payload, rejected_payload, *, descriptor=None,
                             min_reduction=0.0, preserve_evidence=False, min_saved_bytes=1):
    """Find one safe corrective projection, preferring a 20% reduction.

    A caller's minimum is a hard lower bound, not the preferred target.
    The optional absolute byte floor rejects tiny savings even on small
    requests. Both the estimated input and serialized bytes must decrease.
    No provider limit is inferred from these local policy thresholds.
    The original literal, identity, plan and safety contract remain intact.
    """
    if not 0<=min_reduction<1 or type(min_saved_bytes) is not int or min_saved_bytes<1:
        raise ValueError("INVALID_REBUDGET_REDUCTION")

    previous=estimate_payload(rejected_payload)
    def size(item):
        return len(json.dumps(item,ensure_ascii=False,separators=(",",":"),
                              allow_nan=False).encode("utf-8"))

    before=size(rejected_payload)
    # ceil for savings (round down the allowed total) so that the measured
    # reduction can never be smaller than the advertised policy.
    minimum_saved=max(min_saved_bytes,math.ceil(before*min_reduction))
    maximum_bytes=before-minimum_saved
    minimum_tokens=previous-max(1,math.ceil(previous*min_reduction))
    if maximum_bytes<1 or minimum_tokens<1:
        return None
    # Two bounded local projections, but at most ONE corrective HTTP call.
    # If the preferred target cannot be met without cutting protected data,
    # the configured meaningful-saving floor may still permit a retry.
    preferred_bytes=min(maximum_bytes,before-math.ceil(before*.20))
    preferred_tokens=min(minimum_tokens,previous-max(1,math.ceil(previous*.20)))
    targets=((preferred_tokens,preferred_bytes),(minimum_tokens,maximum_bytes))
    for target,body_target in dict.fromkeys(targets):
        if target<1 or body_target<1:
            continue
        try:
            _,payload=fit_provider_payload(context,make_payload,ceiling=target,
                strict=True,descriptor=descriptor,preserve_evidence=preserve_evidence,
                body_ceiling=body_target)
        except ContextBudgetExceeded:
            continue
        if estimate_payload(payload)<=minimum_tokens and size(payload)<=maximum_bytes:
            return payload
    return None


# Local recovery policy, not Groq's documented context window or rate limit.
# Prefer 20%; on an otherwise irreducible envelope, accept only a meaningful
# saving in BOTH relative and absolute terms. The field incident (about
# 46 KB -> 45.7 KB) cannot qualify. A second rejection goes to failover.
GROQ_REBUDGET_MIN_REDUCTION=0.05
GROQ_REBUDGET_MIN_SAVED_BYTES=1024


def compact_groq_rejected_payload(context,make_payload,rejected_payload,*,descriptor=None):
    return compact_rejected_payload(context,make_payload,rejected_payload,
        descriptor=descriptor,preserve_evidence=True,
        min_reduction=GROQ_REBUDGET_MIN_REDUCTION,
        min_saved_bytes=GROQ_REBUDGET_MIN_SAVED_BYTES)
