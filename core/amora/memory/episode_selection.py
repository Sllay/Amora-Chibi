"""MM.00D.3C.1 — bounded selection using the existing memory gate.

Internal Core proposals only. This ranks already durable evidence; it is
not a raw media filter, provider, persistence hook, or durable job queue.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from .episodic_observations import EpisodeSelection, EpisodicObservationConsolidator

_REJECTABLE = frozenset({
    "EPISODE_RECIPIENT_UNKNOWN", "EPISODE_DUPLICATE_EVIDENCE",
    "EPISODE_EVIDENCE_UNAVAILABLE", "EPISODE_EVIDENCE_FORBIDDEN",
    "EPISODE_IDENTITY_UNKNOWN", "EPISODE_UNKNOWN_EVIDENCE",
    "EPISODE_SESSION_MISMATCH", "EPISODE_WINDOW_EXCEEDED",
    "EPISODE_CONTENT_LIMIT",
})


@dataclass(frozen=True, slots=True)
class SelectionOutcome:
    index: int
    reason: str


@dataclass(frozen=True, slots=True)
class EpisodeBatch:
    selected: tuple[EpisodeSelection, ...]
    outcomes: tuple[SelectionOutcome, ...]


class EpisodeBatchSelector:
    """Rank bounded proposals without writing memories or changing scores.

    The caller must authenticate the recipient and authorize the session.
    Selection scores must originate in the Core, never transport metadata.
    Overlap and budget deferrals are not evidence deletion.
    """

    def __init__(self, consolidator: EpisodicObservationConsolidator, *,
                 max_proposals: int = 8, max_selected: int = 4):
        if (type(max_proposals) is not int or type(max_selected) is not int
                or not 1 <= max_selected <= min(max_proposals, 4)
                or not 1 <= max_proposals <= 8):
            raise ValueError("EPISODE_BATCH_LIMIT_INVALID")
        self.consolidator = consolidator
        self.max_proposals = max_proposals
        self.max_selected = max_selected

    def select(self, proposals: tuple[EpisodeSelection, ...], *,
               recipient_id: str, session_id: str, correlation_id: UUID) -> EpisodeBatch:
        if (not isinstance(proposals, tuple) or len(proposals) > self.max_proposals
                or not isinstance(recipient_id, str) or not recipient_id.strip()
                or not isinstance(session_id, str) or not session_id.strip()
                or not isinstance(correlation_id, UUID)):
            raise ValueError("EPISODE_BATCH_INVALID")
        eligible = []
        outcomes = {}
        for index, proposal in enumerate(proposals):
            if not isinstance(proposal, EpisodeSelection):
                outcomes[index] = "EPISODE_PROPOSAL_INVALID"
                continue
            try:
                proposal = EpisodeSelection.model_validate(proposal.model_dump(mode="python"))
                proposal = proposal.model_copy(update={"evidence_ids": tuple(sorted(proposal.evidence_ids))})
            except ValueError:
                outcomes[index] = "EPISODE_PROPOSAL_INVALID"
                continue
            if proposal.recipient_id != recipient_id:
                outcomes[index] = "EPISODE_BATCH_RECIPIENT_MISMATCH"
                continue
            try:
                record_id, candidate = self.consolidator.build_candidate(proposal)
            except ValueError as exc:
                reason = str(exc)
                if reason not in _REJECTABLE:
                    raise
                outcomes[index] = reason
                continue
            if (candidate.provenance.session_id != session_id
                    or candidate.metadata.get("correlation_id") != str(correlation_id)):
                outcomes[index] = "EPISODE_BATCH_SCOPE_MISMATCH"
                continue
            decision = self.consolidator.memory.gate.evaluate(candidate)
            if not decision.store or decision.recommended_kind is None:
                outcomes[index] = "EPISODE_GATE_REJECTED"
                continue
            # Highest existing gate score, then confidence and evidence richness.
            # Tie order is independent of transport arrival and proposal order.
            key = (-decision.score, -candidate.provenance.confidence,
                   -len(proposal.evidence_ids), str(record_id), proposal.model_dump_json())
            eligible.append((key, index, record_id, proposal))

        selected, used, considered = [], set(), set()
        for _, index, record_id, proposal in sorted(eligible):
            if record_id in considered:
                outcomes[index] = "EPISODE_DUPLICATE_PROPOSAL"
                continue
            considered.add(record_id)
            if used.intersection(proposal.evidence_ids):
                outcomes[index] = "EPISODE_OVERLAP_DEFERRED"
                continue
            if len(selected) >= self.max_selected:
                outcomes[index] = "EPISODE_BUDGET_DEFERRED"
                continue
            used.update(proposal.evidence_ids)
            selected.append(proposal)
            outcomes[index] = "EPISODE_SELECTED"
        return EpisodeBatch(tuple(selected), tuple(
            SelectionOutcome(index, outcomes[index]) for index in range(len(proposals))
        ))
