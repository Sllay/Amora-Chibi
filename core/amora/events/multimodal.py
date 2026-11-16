"""MM.00C: internal, typed sensory observations over the existing AmoraEvent.

Not a transport request model. Core must resolve identity, scope and session
from its authenticated boundary before constructing ObservationContext.
This module never publishes, persists, fetches media or calls a provider.
"""
from __future__ import annotations

import json
from enum import StrEnum
from typing import Annotated
from uuid import NAMESPACE_URL, UUID, uuid5

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .models import AmoraEvent, PrivacyScope


Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=256)]
ShortLabel = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=128)]
SemanticText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]


class ObservationSource(StrEnum):
    DISCORD = "discord"
    WHATSAPP = "whatsapp"
    WEB = "web"
    SCREEN = "screen"


class SensoryModality(StrEnum):
    SPEECH = "speech"
    IMAGE = "image"
    SCREEN = "screen"


class EvidenceKind(StrEnum):
    OBSERVED = "observed"
    REPORTED = "reported"
    INFERRED = "inferred"
    UNKNOWN = "unknown"


class UnknownReason(StrEnum):
    GAP = "gap"
    UNAVAILABLE = "unavailable"
    NO_SPEECH = "no_speech"
    LOW_CONFIDENCE = "low_confidence"
    AMBIGUOUS = "ambiguous"


class ObservationContext(BaseModel):
    """Internal trusted context, never populated from unverified worker claims.

    scope_id/stream_id are Core-issued opaque IDs. A session may have multiple
    streams, e.g. a Discord voice speaker and an authorized screen capture.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    source: ObservationSource
    session_id: Identifier
    scope_id: UUID
    stream_id: UUID
    epoch: int = Field(ge=0, le=2**31 - 1, strict=True)
    actor_id: Identifier | None = None
    owner_entity_id: Identifier | None = None
    audience: tuple[Identifier, ...] = Field(default=(), max_length=64)
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    remote_processing_allowed: bool = Field(default=False, strict=True)

    @model_validator(mode="after")
    def validate_scope(self) -> "ObservationContext":
        if self.privacy_scope != PrivacyScope.PUBLIC and not (
            self.owner_entity_id or self.audience
        ):
            raise ValueError("nonpublic observation requires explicit owner or audience")
        if len(set(self.audience)) != len(self.audience):
            raise ValueError("duplicate audience identity")
        if self.remote_processing_allowed and self.privacy_scope in {
            PrivacyScope.PRIVATE, PrivacyScope.SECRET, PrivacyScope.AUTH,
        }:
            raise ValueError("restricted observation cannot permit remote processing")
        return self


class SensoryObservation(BaseModel):
    """Bounded semantic content, not a frame, audio chunk or arbitrary metadata.

    Time coordinates are normalized by Core/adapter; arrival is independent of
    capture ordering because clocks and network delivery can differ.
    """
    model_config = ConfigDict(extra="forbid", frozen=True)

    modality: SensoryModality
    evidence_kind: EvidenceKind
    sequence: int = Field(ge=0, le=2**53 - 1, strict=True)
    capture_started_at: AwareDatetime
    capture_ended_at: AwareDatetime
    received_at: AwareDatetime
    confidence: float = Field(ge=0.0, le=1.0)
    text: SemanticText | None = None
    unknown_reason: UnknownReason | None = None
    provider: ShortLabel | None = None
    model: ShortLabel | None = None
    evidence_id: UUID | None = None
    content_sha256: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    @model_validator(mode="after")
    def validate_observation(self) -> "SensoryObservation":
        if self.capture_ended_at < self.capture_started_at:
            raise ValueError("capture interval is reversed")
        if (self.provider is None) != (self.model is None):
            raise ValueError("provider and model must be paired")
        if self.evidence_kind == EvidenceKind.UNKNOWN:
            if self.text is not None or self.unknown_reason is None:
                raise ValueError("unknown observation requires reason and no semantic claim")
        elif self.text is None or self.unknown_reason is not None:
            raise ValueError("known observation requires text and no unknown reason")
        if self.modality == SensoryModality.SPEECH and self.evidence_kind not in {
            EvidenceKind.REPORTED, EvidenceKind.UNKNOWN,
        }:
            raise ValueError("speech transcript is a report, not an established fact")
        if self.modality != SensoryModality.SPEECH and self.evidence_kind == EvidenceKind.REPORTED:
            raise ValueError("visual observation cannot be a participant speech report")
        return self


def build_observation_event(
    context: ObservationContext, observation: SensoryObservation,
) -> AmoraEvent:
    """Build the canonical envelope for a selected, policy-authorized observation.

    No persistence decision is made here. Callers must apply the existing
    privacy/retention gate before publication; samples and raw media stay out
    of the durable log. Same delivery coordinates yield the same event identity;
    callers must reject conflicting payloads, not treat them as new deliveries.
    """
    if context.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
        raise ValueError("restricted content cannot become a sensory event")
    if observation.evidence_kind == EvidenceKind.REPORTED and not context.actor_id:
        raise ValueError("speech report requires Core-resolved speaker")
    if context.source == ObservationSource.SCREEN and observation.modality != SensoryModality.SCREEN:
        raise ValueError("screen stream must carry screen observations")
    if observation.modality == SensoryModality.SCREEN and context.source != ObservationSource.SCREEN:
        raise ValueError("screen observations require an authorized screen stream")

    identity = json.dumps(
        [context.source.value, context.session_id, str(context.scope_id),
         str(context.stream_id), context.epoch, observation.sequence],
        separators=(",", ":"), ensure_ascii=True,
    )
    event_id = uuid5(NAMESPACE_URL, "amora:perception:v1:" + identity)
    payload = observation.model_dump(mode="json", exclude_none=True)
    # Source/session/actor are the canonical envelope fields; scope and policy
    # travel with the payload so downstream consumers can preserve provenance.
    payload.update(
        version=1, scope_id=str(context.scope_id), stream_id=str(context.stream_id),
        epoch=context.epoch, owner_entity_id=context.owner_entity_id,
        audience=sorted(context.audience),
        remote_processing_allowed=context.remote_processing_allowed,
    )
    return AmoraEvent(
        id=event_id, event_type="perception.observation",
        timestamp=observation.capture_ended_at, source=f"perception:{context.source.value}",
        actor=context.actor_id, confidence=observation.confidence,
        payload=payload, privacy_scope=context.privacy_scope,
        session_id=context.session_id, correlation_id=context.scope_id,
        sequence=observation.sequence, dedupe_key=f"perception:v1:{event_id}",
    )
