"""MM.00D.3B.2A: conservative timing on a shared receiver monotonic clock.

Internal adapter estimates, not trusted client timestamps or a clock protocol.
A calibration is valid only for its authorized stream/epoch and receiver clock.
No capture, persistence, provider calls or automatic fusion.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal
from uuid import UUID

from .models import PrivacyScope
from .multimodal import ObservationContext, ObservationSource, SensoryModality


def _number(value: float) -> bool:
    return type(value) in (int, float) and 0 <= value <= 1e12 and math.isfinite(value)


@dataclass(frozen=True, slots=True)
class TimeInterval:
    earliest: float
    latest: float

    def __post_init__(self):
        if not _number(self.earliest) or not _number(self.latest) or self.latest < self.earliest:
            raise ValueError("SENSORY_TIME_INTERVAL_INVALID")

    @property
    def width(self) -> float:
        return self.latest - self.earliest


@dataclass(frozen=True, slots=True)
class ClockCalibration:
    context: ObservationContext
    receiver_clock_id: UUID
    modality: SensoryModality
    remote_reference: float
    local_reference: TimeInterval
    expires_at: float
    drift_ppm: float = 200.0

    def __post_init__(self):
        if (not isinstance(self.receiver_clock_id, UUID)
                or not isinstance(self.modality, SensoryModality)
                or (self.context.source == ObservationSource.SCREEN) != (self.modality == SensoryModality.SCREEN)
                or not _number(self.remote_reference) or not _number(self.expires_at)
                or not _number(self.drift_ppm) or self.drift_ppm > 10000
                or self.local_reference.width > 10
                or not self.local_reference.latest < self.expires_at <= self.local_reference.latest + 300):
            raise ValueError("SENSORY_CLOCK_CALIBRATION_INVALID")
        # A model_copy(update=...) is not an authorization or validation step.
        validated = ObservationContext.model_validate(self.context.model_dump(mode="python"))
        if validated != self.context:
            raise ValueError("SENSORY_CLOCK_CONTEXT_INVALID")

    def estimate(self, context: ObservationContext, *, remote_start: float,
                 remote_end: float, received_at: float, now: float) -> "CaptureEstimate":
        if context != self.context:
            raise ValueError("SENSORY_CLOCK_CONTEXT_MISMATCH")
        if (not all(_number(v) for v in (remote_start, remote_end, received_at, now))
                or remote_end < remote_start or remote_end - remote_start > 120
                or received_at > now or now < self.local_reference.latest):
            raise ValueError("SENSORY_CAPTURE_TIME_INVALID")
        if now >= self.expires_at:
            raise ValueError("SENSORY_CLOCK_EXPIRED")
        # Do not extrapolate arbitrarily far, including backwards.
        if max(abs(remote_start - self.remote_reference),
               abs(remote_end - self.remote_reference)) > 300:
            raise ValueError("SENSORY_CLOCK_EXTRAPOLATION")
        start = self._map(remote_start)
        end = self._map(remote_end)
        if end.earliest > received_at:
            raise ValueError("SENSORY_CAPTURE_AFTER_ARRIVAL")
        return CaptureEstimate(context, self.receiver_clock_id, self.modality, start, end, received_at)

    def _map(self, remote: float) -> TimeInterval:
        delta = remote - self.remote_reference
        error = abs(delta) * self.drift_ppm / 1_000_000
        return TimeInterval(max(0.0, self.local_reference.earliest + delta - error),
                            self.local_reference.latest + delta + error)


@dataclass(frozen=True, slots=True)
class CaptureEstimate:
    context: ObservationContext
    receiver_clock_id: UUID
    modality: SensoryModality
    start: TimeInterval
    end: TimeInterval
    received_at: float

    def __post_init__(self):
        if (not isinstance(self.receiver_clock_id, UUID)
                or not isinstance(self.modality, SensoryModality)
                or (self.context.source == ObservationSource.SCREEN) != (self.modality == SensoryModality.SCREEN)
                or not _number(self.received_at)
                or self.start.earliest > self.end.earliest
                or self.start.latest > self.end.latest
                or self.end.earliest > self.received_at):
            raise ValueError("SENSORY_CAPTURE_ESTIMATE_INVALID")


@dataclass(frozen=True, slots=True)
class TemporalMatch:
    status: Literal["within_window", "possible", "outside_window", "uncertain"]
    minimum_distance: float
    maximum_distance: float


def _allowed(context: ObservationContext, recipient_id: str) -> bool:
    if context.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
        return False
    return (context.privacy_scope == PrivacyScope.PUBLIC
            or context.owner_entity_id == recipient_id or recipient_id in context.audience)


def correlate_capture(speech: CaptureEstimate, visual: CaptureEstimate, *,
                      recipient_id: str, window: float = 2.0,
                      uncertainty_budget: float = 0.5) -> TemporalMatch:
    """Compare capture intervals conservatively, never infer semantic causality.

    Uses start/end uncertainty separately. 'within_window' means every feasible
    interval pair is close enough, not that speech refers to the visual event.
    """
    if (not isinstance(recipient_id, str) or not recipient_id
            or speech.modality != SensoryModality.SPEECH
            or visual.modality not in {SensoryModality.IMAGE, SensoryModality.SCREEN}
            or not _number(window) or not 0 < window <= 30
            or not _number(uncertainty_budget) or not 0 < uncertainty_budget <= 10):
        raise ValueError("SENSORY_CORRELATION_POLICY_INVALID")
    a, b = speech.context, visual.context
    if (a.session_id != b.session_id or a.correlation_id != b.correlation_id
            or a.stream_id == b.stream_id or speech.receiver_clock_id != visual.receiver_clock_id):
        raise ValueError("SENSORY_CORRELATION_CONTEXT_MISMATCH")
    if not _allowed(a, recipient_id) or not _allowed(b, recipient_id):
        raise ValueError("SENSORY_CORRELATION_FORBIDDEN")
    minimum = max(0.0, visual.start.earliest - speech.end.latest,
                  speech.start.earliest - visual.end.latest)
    maximum = max(0.0, visual.start.latest - speech.end.earliest,
                  speech.start.latest - visual.end.earliest)
    uncertainty = max(speech.start.width, speech.end.width) + max(visual.start.width, visual.end.width)
    if minimum > window:
        status = "outside_window"
    elif uncertainty > uncertainty_budget:
        status = "uncertain"
    elif maximum <= window:
        status = "within_window"
    else:
        status = "possible"
    return TemporalMatch(status, minimum, maximum)
