"""MM.00D.3B.1 — bounded semantic ordering, not a media or durable queue.

Callers must authenticate/revalidate the ObservationContext and close streams
on revocation. No provider, event store, bus, raw audio or frame I/O.
"""
from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass, field
from threading import RLock
from typing import Literal

from amora.time import Clock

from .models import AmoraEvent
from .multimodal import (
    ObservationContext, SensoryObservation, build_observation_event, parse_observation_event,
)


@dataclass(frozen=True, slots=True)
class SequenceGap:
    epoch: int
    first: int
    last: int


@dataclass(frozen=True, slots=True)
class OrderResult:
    status: Literal["delivered", "buffered", "duplicate", "late", "pressure", "gap", "idle"]
    events: tuple[AmoraEvent, ...] = ()
    gaps: tuple[SequenceGap, ...] = ()


@dataclass(slots=True)
class _Pending:
    event: AmoraEvent
    fingerprint: bytes
    size: int
    arrival: float


@dataclass(slots=True)
class _Stream:
    context: ObservationContext
    expected: int
    pending: dict[int, _Pending] = field(default_factory=dict)
    recent: OrderedDict[int, bytes] = field(default_factory=OrderedDict)


def _fingerprint(event: AmoraEvent) -> tuple[bytes, int]:
    payload = event.model_dump(mode="json")
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    # Arrival can change on a retry; all other canonical fields are immutable.
    payload["payload"].pop("received_at")
    identity = json.dumps(payload, sort_keys=True, ensure_ascii=True, separators=(",", ":")).encode()
    return hashlib.sha256(identity).digest(), len(encoded)


class OrderedObservationBuffer:
    """Process-local stream sequencing with bounded pending and replay cache.

    Delivery order is sequence within one authorized epoch, not global capture
    time. Gap controls never become fabricated sensory content.
    """

    def __init__(self, clock: Clock, *, max_streams: int = 64,
                 max_pending_per_stream: int = 32, max_pending_bytes: int = 2 * 1024 * 1024,
                 max_event_bytes: int = 32 * 1024, max_distance: int = 128,
                 recent_capacity: int = 128, reorder_seconds: float = 1.0):
        limits = ((max_streams, 1, 256), (max_pending_per_stream, 1, 256),
                  (max_pending_bytes, 1, 64 * 1024 * 1024), (max_event_bytes, 1, 1024 * 1024),
                  (max_distance, 1, 4096), (recent_capacity, 1, 1024))
        if any(type(value) is not int or not lower <= value <= upper for value, lower, upper in limits):
            raise ValueError("OBSERVATION_BUFFER_LIMIT_INVALID")
        if type(reorder_seconds) not in {int, float} or not 0.01 <= reorder_seconds <= 30:
            raise ValueError("OBSERVATION_BUFFER_WAIT_INVALID")
        self.clock = clock
        self.max_streams = max_streams
        self.max_pending_per_stream = max_pending_per_stream
        self.max_pending_bytes = max_pending_bytes
        self.max_event_bytes = max_event_bytes
        self.max_distance = max_distance
        self.recent_capacity = recent_capacity
        self.reorder_seconds = reorder_seconds
        self._streams: dict[tuple, _Stream] = {}
        self._pending_bytes = 0
        self._lock = RLock()

    @staticmethod
    def _key(context):
        return context.source, context.session_id, context.scope_id, context.stream_id

    @staticmethod
    def _check_sequence(sequence):
        if type(sequence) is not int or not 0 <= sequence <= 2**53 - 1:
            raise ValueError("OBSERVATION_SEQUENCE_INVALID")

    def open(self, context: ObservationContext, *, first_sequence: int = 0) -> None:
        self._check_sequence(first_sequence)
        context = ObservationContext.model_validate(context.model_dump(mode="python"))
        with self._lock:
            key = self._key(context)
            if key in self._streams:
                raise ValueError("OBSERVATION_STREAM_ALREADY_OPEN")
            if len(self._streams) >= self.max_streams:
                raise ValueError("OBSERVATION_STREAM_CAPACITY")
            self._streams[key] = _Stream(context, first_sequence)

    def _state(self, context):
        state = self._streams.get(self._key(context))
        if state is None:
            raise ValueError("OBSERVATION_STREAM_UNKNOWN")
        if state.context != context:
            raise ValueError("OBSERVATION_STREAM_CONTEXT_MISMATCH")
        return state

    def _remember(self, state, event, fingerprint):
        state.recent[event.sequence] = fingerprint
        while len(state.recent) > self.recent_capacity:
            state.recent.popitem(last=False)

    def _release(self, state, item):
        self._remember(state, item.event, item.fingerprint)
        state.expected += 1
        return item.event

    def _drain(self, state):
        delivered = []
        while state.expected in state.pending:
            item = state.pending.pop(state.expected)
            self._pending_bytes -= item.size
            delivered.append(self._release(state, item))
        return delivered

    def push(self, context: ObservationContext, observation: SensoryObservation) -> OrderResult:
        with self._lock:
            state = self._state(context)
            event = build_observation_event(context, observation)
            parse_observation_event(event)
            fingerprint, size = _fingerprint(event)
            sequence = observation.sequence
            if sequence < state.expected:
                previous = state.recent.get(sequence)
                if previous is None:
                    return OrderResult("late")
                if previous != fingerprint:
                    raise ValueError("OBSERVATION_REPLAY_CONFLICT")
                return OrderResult("duplicate")
            previous = state.pending.get(sequence)
            if previous is not None:
                if previous.fingerprint != fingerprint:
                    raise ValueError("OBSERVATION_REPLAY_CONFLICT")
                return OrderResult("duplicate")
            if size > self.max_event_bytes or sequence - state.expected > self.max_distance:
                return OrderResult("pressure")
            item = _Pending(event, fingerprint, size, self.clock.monotonic())
            if sequence == state.expected:
                delivered = [self._release(state, item), *self._drain(state)]
                return OrderResult("delivered", tuple(delivered))
            if (len(state.pending) >= self.max_pending_per_stream
                    or self._pending_bytes + size > self.max_pending_bytes):
                return OrderResult("pressure")
            state.pending[sequence] = item
            self._pending_bytes += size
            return OrderResult("buffered")

    def flush(self, context: ObservationContext) -> OrderResult:
        """Advance expired holes using monotonic elapsed time, not wall clocks."""
        with self._lock:
            state = self._state(context)
            now = self.clock.monotonic()
            delivered, gaps = [], []
            while state.pending:
                oldest = min(item.arrival for item in state.pending.values())
                if now - oldest < self.reorder_seconds:
                    break
                next_sequence = min(state.pending)
                if next_sequence > state.expected:
                    gaps.append(SequenceGap(context.epoch, state.expected, next_sequence - 1))
                    state.expected = next_sequence
                delivered.extend(self._drain(state))
            if gaps:
                return OrderResult("gap", tuple(delivered), tuple(gaps))
            return OrderResult("idle")

    def _discard(self, state):
        discarded = len(state.pending)
        self._pending_bytes -= sum(item.size for item in state.pending.values())
        return discarded

    def reconnect(self, previous: ObservationContext, current: ObservationContext,
                  *, first_sequence: int = 0) -> int:
        """Trusted authority must issue the next epoch; old events are rejected."""
        self._check_sequence(first_sequence)
        current = ObservationContext.model_validate(current.model_dump(mode="python"))
        with self._lock:
            state = self._state(previous)
            if (current.epoch != previous.epoch + 1
                    or current.model_copy(update={"epoch": previous.epoch}) != previous):
                raise ValueError("OBSERVATION_EPOCH_INVALID")
            discarded = self._discard(state)
            self._streams[self._key(previous)] = _Stream(current, first_sequence)
            return discarded

    def close(self, context: ObservationContext) -> int:
        """Drop pending observations; caller must record disconnect/gap if needed."""
        with self._lock:
            state = self._state(context)
            discarded = self._discard(state)
            del self._streams[self._key(context)]
            return discarded

    def counts(self) -> dict[str, int]:
        with self._lock:
            return {
                "streams": len(self._streams),
                "pending": sum(len(state.pending) for state in self._streams.values()),
                "pending_bytes": self._pending_bytes,
                "recent": sum(len(state.recent) for state in self._streams.values()),
            }
