"""Bounded, opt-in-by-intent performance policies, never a new router."""
from __future__ import annotations

from contextvars import ContextVar
from typing import Awaitable, Callable
import re

# Actual transport binds the live guard; provider context alone cannot grant it.
SYNC_REALIZATION_RETRY: ContextVar[tuple[str, Callable[[], Awaitable[bool]]] | None] = ContextVar(
    "amora_sync_realization_retry", default=None)


def attempt_window(remaining: float, later_available: int) -> tuple[float, float]:
    """Allocate bounded fallback opportunity, not a claim about upstream speed.

    Leave up to 12s per available later candidate, at most 30s and 35%
    of the remaining execution budget. Recompute for each attempt.
    """
    reserve = min(30.0, remaining * 0.35, 12.0 * max(0, later_available))
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
        and not context.temporal_opportunity and not context.temporal_context
        and not context.visual_observation
        and "SCHEDULED_REMINDER" not in context.reason_codes
        and _DETAIL_REQUEST.search(context.user_text))
