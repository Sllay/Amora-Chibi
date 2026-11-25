"""Conservative causal context for authorized durable owner text replies.

A related later message is evidence for the current reply, not a new source
of authority. This module never merges jobs, changes original records,
cancels a turn, or claims that an assistant reply was physically delivered.
"""
from __future__ import annotations

import re
import unicodedata

from .temporal import aware_time, observed_timestamp

MAX_FOLLOWUP_SECONDS = 8.0
MAX_FOLLOWUPS = 3
MAX_FOLLOWUP_CHARS = 1800
WINDOW_SECONDS = .65

_CORRECTION = re.compile(r"^(?:amora[, ]+)?(?:corrigindo|correcao|na verdade|quer dizer|quis dizer|digo|retificando)\b")
_CONTINUATION = re.compile(r"^(?:amora[, ]+)?(?:e tambem|alem disso|complementando|continuando|e mais uma coisa|esqueci de dizer)\b")
_INTERRUPTION = re.compile(r"^(?:amora[, ]+)?(?:espera|pera(?:i)?|calma|nao responde ainda|para|pare|cancela)\b")
_TOPIC_CHANGE = re.compile(r"^(?:amora[, ]+)?(?:mudando de assunto|outro assunto|agora outra coisa|outra pergunta)\b")
_OPEN_END = re.compile(r"(?:\.\.\.|…|[,;:]|\b(?:e|mas|porque|tipo|seguinte))\s*$", re.I)
_STOP = frozenset("amora quero queria voce isso isto essa esse agora tambem verdade dizer coisa uma para com por que nao dos das mais sobre como qual quais entao".split())


def _fold(text):
    return "".join(c for c in unicodedata.normalize("NFKD", text.casefold())
                   if not unicodedata.combining(c)).strip()


def _terms(text):
    return {t for t in re.findall(r"\w+", _fold(text)) if 3 <= len(t) <= 64 and t not in _STOP}


def followup_kind(previous: str, current: str) -> str:
    """Lexical hints only, not a semantic classifier or cancellation grant."""
    value = _fold(current)
    if _TOPIC_CHANGE.search(value):
        return "topic_change"
    if _INTERRUPTION.search(value):
        return "interruption"
    overlap = _terms(previous) & _terms(current)
    if _CORRECTION.search(value):
        return "correction" if overlap else "uncertain_correction"
    if _CONTINUATION.search(value):
        return "continuation" if overlap else "uncertain_continuation"
    return "new_question" if "?" in value else "independent"


def composition_window(text: str) -> float:
    # Do not delay an ordinary complete message. A clear unfinished clause
    # can afford one short, bounded wait before compiling the context.
    return WINDOW_SECONDS if text.strip() and _OPEN_END.search(text.strip()) else 0.0


def causal_partition(request, messages, *, allowed):
    """Separate later input from history using the persisted current source.

    Ties are ambiguous, not silently ordered by a random UUID. Only strict
    later, same-session, same-speaker received inputs may become followups;
    stop at an independent/ambiguous turn. All rows remain in canonical store.
    """
    current_id = str(request.turn_id)
    anchor = next((m for m in messages if str(m.turn_id) == current_id
                   and m.dedupe_key == "cognitive-input:" + current_id
                   and m.speaker_id == request.speaker_id
                   and m.session_id == request.session_id), None)
    if anchor is None:
        return [], [], "anchor_missing"
    stamp, _ = observed_timestamp(anchor)
    if stamp is None:
        return [], [], "anchor_missing"
    later = []
    excluded = set()
    for m in messages:
        ident = str(m.turn_id)
        if ident == current_id or m.speaker_id == "ENTITY_AMORA":
            continue
        if m.session_id != request.session_id:
            continue
        at, _ = observed_timestamp(m)
        if at is None or at >= stamp:
            excluded.add(ident)
            if at is not None and at > stamp:
                later.append((at, ident, m))
    history = [m for m in messages if str(m.turn_id) not in excluded]
    followups = []
    remaining = MAX_FOLLOWUP_CHARS
    previous = anchor.raw_text or request.text
    seen = set()
    # Equal timestamps among later rows have no proven ordering. Stop rather
    # than treating an arbitrary order as a sequence of corrections.
    for at, ident, m in sorted(later, key=lambda item: (item[0], item[1])):
        if ident in seen:
            continue
        seen.add(ident)
        if sum(other_at == at and other_id != ident for other_at, other_id, _ in later):
            break
        if (at - stamp).total_seconds() > MAX_FOLLOWUP_SECONDS:
            break
        if (m.speaker_id != request.speaker_id or m.addressee_id != request.target_id
                or m.status.value != "received" or m.dedupe_key != "cognitive-input:" + ident
                or m.privacy_scope.value != "PERSONAL" or not m.remote_processing_allowed
                or not m.metadata.get("reply_enqueue_requested")
                or m.metadata.get("test_mode") or not allowed(m)):
            break
        literal = m.raw_text or ""
        kind = followup_kind(previous, literal)
        if kind not in {"correction", "continuation"} or not literal or len(literal) > remaining:
            break
        followups.append({"turn_id": ident, "observed_at": at.isoformat(),
                          "relation_hint": kind, "literal_text": literal,
                          "independent_reply_still_pending": True})
        remaining -= len(literal)
        previous = literal
        if len(followups) >= MAX_FOLLOWUPS:
            break
    return history, followups, "partitioned"
