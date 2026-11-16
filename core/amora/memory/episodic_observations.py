"""MM.00D.2: selected, durable sensory evidence -> existing episodic memory.

Internal only. Selection and requester identity must come from trusted Core
admission. This does not subscribe to the event bus or schedule reflection.
"""
from __future__ import annotations

import json
from datetime import timedelta
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from amora.events import PrivacyScope
from amora.events.multimodal import EvidenceKind, parse_observation_event
from amora.storage.database import AmoraDatabase

from .models import MemoryCandidate, MemoryKind, MemoryProvenance, ProvenanceKind
from .service import MemoryService, RememberResult


_MAX_EPISODE_WINDOW = timedelta(seconds=120)
_SCOPE_RANK = {scope: rank for rank, scope in enumerate((
    PrivacyScope.PUBLIC, PrivacyScope.SOCIAL, PrivacyScope.PERSONAL,
    PrivacyScope.PRIVATE, PrivacyScope.SECRET, PrivacyScope.AUTH,
))}
_Score = Annotated[float, Field(ge=0.0, le=1.0, strict=True)]
_Person = Annotated[str, StringConstraints(strict=True, strip_whitespace=True,
                                           min_length=1, max_length=256)]


class EpisodeSelection(BaseModel):
    """Explicit Core selection, not provider authority or a public request."""
    model_config = ConfigDict(extra="forbid", frozen=True)

    evidence_ids: tuple[UUID, ...] = Field(min_length=1, max_length=4)
    recipient_id: _Person
    importance: _Score = 0.5
    novelty: _Score = 0.5
    emotional_weight: _Score = 0.0
    relationship_relevance: _Score = 0.0
    future_consequence: _Score = 0.0
    rarity: _Score = 0.0
    goal_relevance: _Score = 0.0


def episode_record_id(evidence_ids: tuple[UUID, ...]) -> UUID:
    """Stable identity of a selected evidence set; independent of ordering."""
    keys = sorted(str(event_id) for event_id in evidence_ids)
    return uuid5(NAMESPACE_URL, "amora:episode:v1:" + json.dumps(keys, separators=(",", ":")))


def _effective_audience(context):
    allowed = set(context.audience)
    if context.owner_entity_id:
        allowed.add(context.owner_entity_id)
    return allowed


class EpisodicObservationConsolidator:
    """No parallel memory store, no automatic recording, no remote inference."""

    def __init__(self, database: AmoraDatabase, memory: MemoryService):
        self.database = database
        self.memory = memory

    def build_candidate(self, selection: EpisodeSelection) -> tuple[UUID, MemoryCandidate]:
        identities = self.memory.store.identities
        if identities.get(selection.recipient_id) is None:
            raise ValueError("EPISODE_RECIPIENT_UNKNOWN")
        if len(set(selection.evidence_ids)) != len(selection.evidence_ids):
            raise ValueError("EPISODE_DUPLICATE_EVIDENCE")

        evidence = []
        for event_id in selection.evidence_ids:
            event = self.database.get_observation_event(event_id)
            if event is None:
                raise ValueError("EPISODE_EVIDENCE_UNAVAILABLE")
            context, observation = parse_observation_event(event)
            if context.privacy_scope != PrivacyScope.PUBLIC and selection.recipient_id not in _effective_audience(context):
                raise ValueError("EPISODE_EVIDENCE_FORBIDDEN")
            # All nonpublic audience members and attributed actors must have
            # resolved identities. A display name is not authority.
            identity_ids = _effective_audience(context)
            if context.actor_id:
                identity_ids.add(context.actor_id)
            if any(identities.get(person) is None for person in identity_ids):
                raise ValueError("EPISODE_IDENTITY_UNKNOWN")
            if observation.evidence_kind == EvidenceKind.UNKNOWN:
                raise ValueError("EPISODE_UNKNOWN_EVIDENCE")
            evidence.append((event, context, observation))

        # Selection order must not alter identity, chronology or content.
        evidence.sort(key=lambda item: (item[2].capture_started_at, item[2].capture_ended_at, str(item[0].id)))
        first, first_context, _ = evidence[0]
        if any(ctx.session_id != first_context.session_id or ctx.correlation_id != first_context.correlation_id
               for _, ctx, _ in evidence):
            raise ValueError("EPISODE_SESSION_MISMATCH")
        start = min(obs.capture_started_at for _, _, obs in evidence)
        end = max(obs.capture_ended_at for _, _, obs in evidence)
        if end - start > _MAX_EPISODE_WINDOW:
            raise ValueError("EPISODE_WINDOW_EXCEEDED")

        scope = max((ctx.privacy_scope for _, ctx, _ in evidence), key=_SCOPE_RANK.__getitem__)
        private_sources = [ctx for _, ctx, _ in evidence if ctx.privacy_scope != PrivacyScope.PUBLIC]
        audience = set.intersection(*(_effective_audience(ctx) for ctx in private_sources)) if private_sources else set()
        if scope != PrivacyScope.PUBLIC and selection.recipient_id not in audience:
            raise ValueError("EPISODE_EVIDENCE_FORBIDDEN")
        owners = {ctx.owner_entity_id for ctx in private_sources}
        owner = next(iter(owners)) if len(owners) == 1 else None
        if owner not in audience:
            owner = None
        remote = all(ctx.remote_processing_allowed for _, ctx, _ in evidence)
        if scope in {PrivacyScope.PRIVATE, PrivacyScope.SECRET, PrivacyScope.AUTH}:
            remote = False

        # Preserve exact selected text and label its epistemic status. JSON
        # quoting keeps line breaks/quotes inside evidence, not new provenance.
        labels = {EvidenceKind.OBSERVED: "observado", EvidenceKind.REPORTED: "relatado",
                  EvidenceKind.INFERRED: "inferido (não confirmado)"}
        lines = []
        refs = {}
        details = []
        for event, ctx, obs in evidence:
            actor = ctx.actor_id
            if actor:
                refs[actor] = min(refs.get(actor, 1.0), obs.confidence)
            lines.append(
                f"[{obs.capture_started_at.isoformat()} | {obs.modality.value} | "
                f"{labels[obs.evidence_kind]} | ator={json.dumps(actor, ensure_ascii=False)}] "
                f"{json.dumps(obs.text, ensure_ascii=False)}"
            )
            details.append({
                "event_id": str(event.id), "source": ctx.source.value,
                "modality": obs.modality.value, "evidence_kind": obs.evidence_kind.value,
                "actor_id": actor, "confidence": obs.confidence,
                "capture_started_at": obs.capture_started_at.isoformat(),
                "capture_ended_at": obs.capture_ended_at.isoformat(),
            })
        content = "\n".join(lines)
        if len(content) > 12000:
            raise ValueError("EPISODE_CONTENT_LIMIT")

        event_ids = sorted(str(event.id) for event, _, _ in evidence)
        record_id = episode_record_id(tuple(event.id for event, _, _ in evidence))
        candidate = MemoryCandidate(
            kind=MemoryKind.EPISODIC, content=content,
            provenance=MemoryProvenance(
                kind=ProvenanceKind.DERIVED, source="perception:episode:v1",
                confidence=min(obs.confidence for _, _, obs in evidence),
                source_event_id=first.id, session_id=first_context.session_id,
            ),
            privacy_scope=scope, owner_entity_id=owner, audience=audience,
            remote_processing_allowed=remote, entity_refs=refs,
            tags={"multimodal", *(obs.modality.value for _, _, obs in evidence)},
            importance=selection.importance, novelty=selection.novelty,
            emotional_weight=selection.emotional_weight,
            relationship_relevance=selection.relationship_relevance,
            future_consequence=selection.future_consequence, rarity=selection.rarity,
            goal_relevance=selection.goal_relevance,
            metadata={
                "episode_version": 1, "correlation_id": str(first_context.correlation_id),
                "evidence_event_ids": event_ids, "evidence": details,
                "capture_started_at": start.isoformat(), "capture_ended_at": end.isoformat(),
            },
        )
        return record_id, candidate

    def consolidate(self, selection: EpisodeSelection) -> RememberResult:
        record_id, candidate = self.build_candidate(selection)
        # The gate decides relevance; atomic create-only mode protects
        # concurrent writers and previously archived/edited records.
        return self.memory.remember(candidate, record_id=record_id, insert_only=True)
