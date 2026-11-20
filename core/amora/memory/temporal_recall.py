"""Owner-only, source-grounded date recall for historical conversations.

This parser identifies *queries*, not authority. Messaging identity/floor gates
must authorize the requester before a single historical record is read. No LLM
is required to interpret a calendar date or to quote persisted dialogue.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone

from amora.cognition.temporal import conversation_zone
from .recall_intent import normalize_memory_request, topic_terms


_RECALL = re.compile(
    r"^(?:(?:(?:voce|vc|tu)\s+)?(?:se\s+)?(?:lembra|lembras|recorda|recordas)\b"
    r"|(?:o\s+que|que|qual\s+(?:foi|era))\s+"
    r"(?:(?:eu|voce|vc|tu|a\s+gente|nos)\s+)?"
    r"(?:(?:te|me)\s+)?(?:falei|cont(ei|amos)|disse|respond(eu|i)|falamos|conversamos)\b"
    r"|(?:o\s+que|qual)\s+(?:(?:voce|vc|tu)\s+)?(?:me\s+)?(?:respondeu|disse)\b)"
)
_DATE = re.compile(r"(?<!\d)(?:\b(?:no|em|na)\s+)?(?:\bdia\s+)?"
                   r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?(?!\d)")
_ASK_REPLY = re.compile(
    r"(?:\b(?:voce|vc|tu)\s+(?:me\s+)?(?:respondeu|disse|falou)\b"
    r"|\b(?:tua|sua)\s+resposta\b|\bo\s+que\s+(?:vc|voce|tu)\s+me\s+respondeu\b)"
)
_QUESTION_WORDS = frozenset({
    "lembra", "lembras", "recorda", "recordas", "eu", "te", "me", "falei",
    "cont(ei", "contei", "contamos", "disse", "respondeu", "respondi", "falamos",
    "conversamos", "era", "foi", "qual", "dia", "data", "aquele", "aquela",
    "estavamos", "voce", "tua", "sua", "resposta", "naquele", "naquela",
})


@dataclass(frozen=True)
class TemporalRecallIntent:
    day: date
    topic: frozenset[str]
    assistant_only: bool = False

    def utc_bounds(self) -> tuple[datetime, datetime]:
        zone = conversation_zone()
        start = datetime.combine(self.day, time.min, tzinfo=zone)
        return start.astimezone(timezone.utc), (start + timedelta(days=1)).astimezone(timezone.utc)

    @property
    def day_label(self) -> str:
        return self.day.strftime("%d/%m/%Y")


def parse_temporal_recall(text: str, now: datetime) -> TemporalRecallIntent | None:
    normalized = normalize_memory_request(text)
    if normalized is None or _RECALL.match(normalized) is None or len(normalized) > 240:
        return None
    matches = list(_DATE.finditer(normalized))
    if len(matches) != 1:
        return None
    stamp = matches[0]
    local_now = now.astimezone(conversation_zone())
    try:
        month, day = int(stamp[2]), int(stamp[1])
        year = int(stamp[3]) if stamp[3] is not None else local_now.year
        candidate = date(year, month, day)
        # Without an explicit year, refer to the most recent occurrence,
        # never a future date inferred from the current year.
        if stamp[3] is None and candidate > local_now.date():
            candidate = date(year - 1, month, day)
    except (ValueError, OverflowError):
        return None
    without_date = normalized[:stamp.start()] + " " + normalized[stamp.end():]
    terms = topic_terms(without_date) - _QUESTION_WORDS
    if len(terms) > 8:
        return None
    return TemporalRecallIntent(candidate, terms, assistant_only=bool(_ASK_REPLY.search(normalized)))
