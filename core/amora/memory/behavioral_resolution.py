"""Deterministic projection of source-grounded social feedback.

Never edits/archives history or treats an LLM-proposed topic as authority.
Only owner-scoped, eligible records that have already passed MemoryQuery's
privacy and remote-use fences can enter this local ranking.
"""
from __future__ import annotations

from datetime import datetime

from .models import MemoryHit


_KNOWN_TOPICS = frozenset({
    "question_repetition", "response_length", "initiative_frequency",
    "address_style", "humor", "sticker_usage",
})
_KNOWN_SIGNALS = frozenset({"speech_preference", "behavioral_correction"})


def _timestamp(memory) -> datetime:
    raw = memory.metadata.get("source_observed_at")
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
            if parsed.tzinfo is not None and parsed.utcoffset() is not None:
                return parsed
        except (ValueError, TypeError):
            pass
    return memory.created_at


def resolve_behavioral_guidance(
    hits: list[MemoryHit], *, owner_id: str, limit: int = 2,
) -> list[MemoryHit]:
    """Pick newest nonconflicting preferences with no new storage round-trip.

    Only an explicit, *general* correction of a classified topic supersedes
    earlier general feedback for that topic. Contextual preferences remain
    independent; 'other' and older unclassified memories are never
    auto-contradicted. Old memories remain immutable, for audit/revocation.
    """
    if not owner_id or limit <= 0:
        return []
    bounded = min(limit, 3)
    eligible = []
    for hit in hits:
        item = hit.memory
        if ("TAGGED_SUPPLEMENT" not in hit.reason_codes
                or "amora_behavioral_feedback" not in item.tags
                or item.owner_entity_id != owner_id
                or not item.remote_processing_allowed
                or item.archived_at is not None
                or item.metadata.get("behavioral_source") != "verified_owner_dm_feedback"
                or item.metadata.get("behavioral_signal") not in _KNOWN_SIGNALS):
            continue
        eligible.append(hit)

    eligible.sort(
        key=lambda hit: (_timestamp(hit.memory), hit.memory.created_at,
                         str(hit.memory.id)), reverse=True,
    )
    selected: list[MemoryHit] = []
    seen_general_topics: set[str] = set()
    seen_exact: set[str] = set()
    for hit in eligible:
        memory = hit.memory
        topic = memory.metadata.get("behavioral_topic")
        scope = memory.metadata.get("behavioral_scope")
        # Only the explicitly classified general scope is eligible for
        # same-topic latest-wins. Older records lacking these fields survive.
        general_topic = (
            topic if scope == "general" and topic in _KNOWN_TOPICS else None
        )
        content_key = memory.content.casefold().strip()
        if content_key in seen_exact or general_topic in seen_general_topics:
            continue
        seen_exact.add(content_key)
        if general_topic:
            seen_general_topics.add(general_topic)
        selected.append(hit)
        if len(selected) == bounded:
            break
    return selected
