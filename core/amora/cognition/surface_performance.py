"""Bounded, opt-in-by-intent performance policies, never a new router."""
from __future__ import annotations

from contextvars import ContextVar
from typing import Awaitable, Callable
import re

# Actual transport binds the live guard; provider context alone cannot grant it.
SYNC_REALIZATION_RETRY: ContextVar[tuple[str, Callable[[], Awaitable[bool]]] | None] = ContextVar(
    "amora_sync_realization_retry", default=None)


INTERACTIVE_PROVIDER_ATTEMPT: ContextVar[bool] = ContextVar(
    "amora_interactive_provider_attempt",default=False)


def attempt_window(remaining: float, later_available: int, *, measured_reserves=None) -> tuple[float, float]:
    """Allocate bounded fallback opportunity, not a claim about upstream speed.

    Leave up to 12s per available later candidate, at most 30s and 35%
    of the remaining execution budget. Recompute for each attempt.
    """
    desired=12.0*max(0,later_available)
    if measured_reserves is not None and len(measured_reserves)==later_available:
        desired=sum(max(6.0,min(18.0,float(value))) for value in measured_reserves)
    reserve = min(30.0, remaining * 0.35, desired)
    return max(0.0, remaining - reserve), reserve


_DETAIL_REQUEST = re.compile(
    r"\b(?:documenta[cç][aã]o\s+(?:t[eé]cnica|detalhada|completa)|"
    r"(?:manual|especifica[cç][aã]o|guia)\s+(?:t[eé]cnic[ao]|complet[ao]|detalhad[ao])|"
    r"n[aã]o\s+(?:resuma|omita)|sem\s+(?:resumir|omitir)|"
    r"explique\s+(?:cada|todos\s+os|todas\s+as)\s+(?:sistemas?|m[oó]dulos?|etapas?|componentes?))\b", re.I)


def detailed_output_requested(context) -> bool:
    """Explicit request, not ordinary chatter, reflection or visual evidence."""
    return bool(context.scene_visibility == "private"
        and context.remote_processing_allowed
        and context.privacy_scope.value == "PERSONAL"
        and context.speaker_id == "PERSON_BK" and context.target_id == "ENTITY_AMORA"
        # Ordinary clock projection is present on normal Discord turns.
        and not context.temporal_opportunity
        and not context.visual_observation
        and not {"SCHEDULED_REMINDER","COGNITIVE_REFLECTION","AUTONOMOUS_INITIATION"}.intersection(context.reason_codes)
        and _DETAIL_REQUEST.search(context.user_text))


class ProviderPerformance:
    """Bounded process-local evidence, isolated by model and request class.

    Explicit primary/secondary tiers remain authoritative. Cold or expired
    evidence never changes configured order; no synthetic requests are sent.
    """
    def __init__(self, clock):
        from collections import OrderedDict
        self.clock = clock
        self.samples = OrderedDict()

    def observe(self, descriptor, workload, elapsed_seconds, success):
        import math
        from collections import deque
        if not math.isfinite(elapsed_seconds) or elapsed_seconds < 0:
            return
        key = (descriptor.name, descriptor.model, workload)
        rows = self.samples.setdefault(key, deque(maxlen=12))
        rows.append((self.clock.now(), min(300.0, elapsed_seconds), bool(success)))
        self.samples.move_to_end(key)
        while len(self.samples) > 32:
            self.samples.popitem(last=False)

    def evidence(self, descriptor, workload):
        from datetime import timedelta
        from statistics import median
        now = self.clock.now()
        rows = [row for row in self.samples.get((descriptor.name,descriptor.model,workload),())
                if timedelta(0) <= now-row[0] <= timedelta(minutes=30)]
        if len(rows) < 3:
            return None
        successes = [row[1] for row in rows if row[2]]
        # Penalize observed failed attempts, not guessed upstream rate limits.
        return (median(successes or [row[1] for row in rows])
                + 45 * (1-len(successes)/len(rows)), len(rows))

    def fallback_reserve(self,descriptor,workload):
        from datetime import timedelta
        from statistics import median
        now=self.clock.now()
        rows=[elapsed for at,elapsed,ok in self.samples.get(
            (descriptor.name,descriptor.model,workload),())
              if ok and timedelta(0)<=now-at<=timedelta(minutes=30)]
        # Only observed successful latencies from this model/task. The
        # reserve is an opportunity, not a prediction or guarantee.
        return max(6.0,min(18.0,median(rows)+2)) if len(rows)>=3 else 12.0

    def rank(self, candidates, workload, *, quota):
        # Compare evidence only within a preference tier. Unobserved candidates
        # keep their original position rather than being promoted by guesswork.
        result = list(candidates)
        def tier(d):
            return (bool(d.metadata.get("primary_social_preference")),
                    bool(d.metadata.get("social_secondary_preference")))
        for preference in dict.fromkeys(tier(d) for d in result):
            slots = [i for i,d in enumerate(result) if tier(d)==preference
                     and quota.remaining(d.name,model=d.model)<=0 and self.evidence(d,workload)]
            ranked = sorted((result[i] for i in slots),
                            key=lambda d:self.evidence(d,workload)[0])
            if slots and self.evidence(result[slots[0]],workload)[0] <= self.evidence(ranked[0],workload)[0]*1.2:
                continue
            for i,d in zip(slots,ranked):
                result[i]=d
        return tuple(result)

    def snapshot(self):
        return {"tracked_model_workloads":len(self.samples),
                "sample_count":sum(len(rows) for rows in self.samples.values()),
                "retention_seconds":1800}
