"""Conservative causal context for authorized durable owner text replies.

A related later message is evidence for the current reply, not a new source
of authority. This module never merges jobs, changes original records,
cancels a turn, or claims that an assistant reply was physically delivered.
"""
from __future__ import annotations

import re
import unicodedata
from contextvars import ContextVar

from .temporal import aware_time, observed_timestamp

# Bound only by the authorized durable dispatcher, never by user metadata.
GROUP_RELATED_TURN: ContextVar[str | None] = ContextVar("amora_group_related_turn", default=None)

def grouping_enabled():
    import os
    return os.getenv("AMORA_REALTIME_CAUSAL_GROUPING_ENABLED","0").strip().lower() in {"1","true","yes","on"}

MAX_FOLLOWUP_SECONDS = 8.0
MAX_FOLLOWUPS = 3
MAX_FOLLOWUP_CHARS = 1800
WINDOW_SECONDS = .65

_CORRECTION = re.compile(r"^(?:amora[, ]+)?(?:corrigindo|correcao|na verdade|quer dizer|quis dizer|digo|retificando)\b")
_CONTINUATION = re.compile(r"^(?:amora[, ]+)?(?:e tambem|alem disso|complementando|continuando|e mais uma coisa|esqueci de dizer)\b")
_INTERRUPTION = re.compile(r"^(?:amora[, ]+)?(?:espera|pera(?:i)?|calma|nao responde ainda|para|pare|cancela)\b")
_TOPIC_CHANGE = re.compile(r"^(?:amora[, ]+)?(?:mudando de assunto|outro assunto|agora outra coisa|outra coisa|outra pergunta)\b")
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


_SEPARATE_REQUEST = re.compile(
    r"\b(?:explique|explica|pesquise|pesquisa|mostre|mostra|envie|manda|envia|"
    r"faca|crie|gere|lembre|lembra|salve|guarde|agenda|agende|cancela|cancele|"
    r"responda|analise|compare|abre|abra|acesse|acessa|quero saber|como funciona|"
    r"qual|quais|quanto|quando|onde|por que|porque)\b")
def groupable_statement(text: str) -> bool:
    value = _fold(text)
    return bool(value and len(value)<=600 and "?" not in value and "\n" not in value
                and not _SEPARATE_REQUEST.search(value)
                and not re.search(r"[;.!]\s+\S", value)
                and not re.search(r"\b(?:outra coisa|outro assunto|mudando de assunto)\b",value))


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
    # An input with exactly the anchor timestamp has no proven causal order.
    # Do not skip over it and attach a later correction to the wrong turn.
    if any(str(m.turn_id) != current_id and m.session_id == request.session_id
           and m.speaker_id != "ENTITY_AMORA" and observed_timestamp(m)[0] == stamp
           for m in messages):
        return history, [], "partitioned"
    followups = []
    group_prefix = GROUP_RELATED_TURN.get() == current_id
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
        group_prefix = group_prefix and groupable_statement(literal)
        followups.append({"turn_id": ident, "observed_at": at.isoformat(),
                          "relation_hint": kind, "literal_text": literal,
                          "independent_reply_still_pending": not group_prefix})
        remaining -= len(literal)
        previous = literal
        if len(followups) >= MAX_FOLLOWUPS:
            break
    return history, followups, "partitioned"


# Extract whole bounded clauses, not model-generated summaries. The underlying
# turn remains the authority; these excerpts only call attention to negation.
_NEGATIVE_TASK = re.compile(
    r"\b(?:n[aã]o\s+(?:pode(?:m)?|deve(?:m)?|quero|use|usar|fa[cç]a|fazer|"
    r"permita|permitir|inclua|incluir|recomende|recomendar|sugira|sugerir)|"
    r"nenhum[ae]s?(?:\s+d[ae]l[ae]s)?\s+(?:pode(?:m)?|deve(?:m)?)|"
    r"proibid[oa]s?|exclusiv[oa]s?|exclusivamente|somente|apenas|"
    r"s[oó]\s+(?:pode(?:m)?|deve(?:m)?|use|usar|fa[cç]a|produz(?:em)?|produzir))\b", re.I)


def literal_negative_constraints(text: str, followups=()) -> list[dict[str, str]]:
    """Bounded task guidance; no authority or semantic enforcement is minted."""
    rows = [("current", text)]
    rows.extend((str(item.get("turn_id") or ""), item.get("literal_text", ""))
                for item in followups if isinstance(item, dict)
                and item.get("relation_hint") in {"correction", "continuation"})
    result = []
    seen = set()
    for source, literal in rows:
        if not isinstance(literal, str):
            continue
        for clause in re.split(r"(?<=[.!?;])\s+|\n+", literal[:12000]):
            clause = clause.strip()
            if (not clause or clause.endswith("?") or len(clause) > 360
                    or not _NEGATIVE_TASK.search(clause) or clause in seen):
                continue
            seen.add(clause)
            result.append({"source_turn": source, "literal": clause})
            if len(result) >= 6:
                return result
    return result
