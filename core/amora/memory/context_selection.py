"""Budgeted, source-faithful conversation continuity for verified private DMs.

Pure selection only: this code never creates a memory, relaxes privacy gates,
rewrites source utterances, or authorizes remote provider reuse.
"""
from __future__ import annotations

import re

from .recall_intent import normalize_recall, topic_terms


_SOCIAL_ONLY = re.compile(
    r"^(?:(?:oi+|ola+|opa+|eai+|iae+|iae|salve|hey|hello|"
    r"bom dia|boa tarde|boa noite|tudo bem|"
    r"sim|ss|nao|n|ok|okay|blz|beleza|certo|show|valeu|obrigado|obrigada|"
    r"continue|prossiga|pode continuar|entendi|aham|uhum|"
    r"kkkk+|ksksks+|haha+)[!?.\s]*)$"
)
# Acknowledgement + continuation is still the same conversation turn. It is
# not evidence for selecting unrelated long-term memories. Require a complete
# match so project-specific requests ("continue a animação do coelho") still
# receive normal topic retrieval.
_BRIEF_CONTINUATION = re.compile(
    r"^(?:(?:sim|ss|ok|okay|certo|beleza|blz|ta|entao)[,!.\s]+)?"
    r"(?:continue|continua|prossiga|segue|pode continuar|pode prosseguir|pode seguir)"
    r"[!?.\s]*$"
)


_STOP = frozenset({
    "lembra", "lembrar", "recorda", "recordar", "acho", "penso",
    "diz", "disse", "falei", "falamos", "contei", "me", "te", "de",
    "estou", "estava", "estar", "sera", "vai", "ficou", "foi",
    "fazer", "faz", "faca", "sobre", "meu", "minha", "vc",
    "voce", "isso", "aqui", "hoje", "ontem", "amanha", "ontem",
    "tudo", "aquilo", "nesse", "nisso", "dela", "dele", "esse",
    "essa", "aquela", "aquele", "como", "quando", "qual", "quero",
    "mesmo", "realmente", "coisa", "parte", "agora", "entao",
    "tava", "tipo", "falando", "sobre", "ela", "ele", "gente",
})


def is_social_only(text: str) -> bool:
    normalized = " ".join(normalize_recall(text).strip(" !?.").split())
    return bool(_SOCIAL_ONLY.fullmatch(normalized) or _BRIEF_CONTINUATION.fullmatch(normalized))


def subject_terms(text: str) -> frozenset[str]:
    # Very small utterances and colloquial followups are anchored by their
    # most recent exchange, not by lexical matches to unrelated memories.
    if is_social_only(text):
        return frozenset()
    terms = topic_terms(text) - _STOP
    return frozenset(term for term in terms if len(term) >= 3)


def select_memories(memories, text: str):
    """Keep relevant evidence first; preserve limited fallback for vague talk."""
    if is_social_only(text):
        return []
    terms = subject_terms(text)
    if not terms:
        return list(memories[:2])
    scored = []
    for index, memory in enumerate(memories):
        overlap = len(terms & topic_terms(memory.content))
        if overlap:
            scored.append((-overlap, index, memory))
    if scored:
        scored.sort(key=lambda x: (x[0], x[1]))
        relevant = [item for _, _, item in scored[:5]]
        # One independent high-ranked contextual memory can help resolve
        # pronouns without turning ordinary responses into encyclopedic dumps.
        if len(relevant) < 3:
            secondary = next((m for m in memories if m not in relevant), None)
            if secondary is not None:
                relevant.append(secondary)
        return relevant[:5]
    return list(memories[:2])


def select_recent(recent: list[dict], text: str) -> tuple[list[dict], int]:
    """Keep latest causal dialogue and up to two older matched conversation turns.

    All inputs are already filtered by Core's identity, session, test and
    privacy gates. Preserve chronological order, without rewrites or invented
    quotations. No messages are searched outside the existing bounded corpus.
    """
    if not recent:
        return [], 0
    anchor_count = 8 if is_social_only(text) else 10
    if len(recent) <= anchor_count:
        return recent, 0
    latest_start = len(recent) - anchor_count
    keep = set(range(latest_start, len(recent)))
    terms = subject_terms(text)
    if terms:
        matched_turns = []
        for i in range(latest_start):
            value = recent[i]
            if (value.get("speaker_id") and value.get("turn_id")
                    and terms & topic_terms(str(value.get("text") or ""))):
                key = value["turn_id"]
                if key not in matched_turns:
                    matched_turns.append(key)
        for turn_id in matched_turns[-2:]:
            indexes = [
                i for i in range(latest_start)
                if recent[i].get("turn_id") == turn_id
            ]
            # Include both participants for a historical turn, bounded to
            # prevent one unusually long spoken turn from consuming the budget.
            keep.update(indexes[:4])
    selected = [record for index, record in enumerate(recent) if index in keep]
    # Keep the newest 10 even when the historical query is very long.
    return selected, max(0, len(selected) - anchor_count)


def measured_projection_chars(memories, recent) -> int:
    return sum(len(m.content) for m in memories) + sum(
        len(str(m.get("text") or "")) for m in recent
    )
