"""MM.00D.3B.1 — semantic ordering and bounded backpressure."""
from datetime import timedelta
from uuid import UUID

import pytest

from amora.events.ordered_observations import OrderedObservationBuffer, SequenceGap
from amora.time import SimulatedClock
from test_multimodal_contract_mm00 import NOW, context, observation


def obs(sequence, **fields):
    return observation(sequence=sequence, **fields)


def test_out_of_order_and_replay_preserve_first_arrival():
    clock = SimulatedClock(NOW)
    buf = OrderedObservationBuffer(clock)
    ctx = context()
    buf.open(ctx)
    first = obs(1)
    assert buf.push(ctx, first).status == "buffered"
    assert buf.push(ctx, obs(1, received_at=NOW + timedelta(seconds=5))).status == "duplicate"
    with pytest.raises(ValueError, match="REPLAY_CONFLICT"):
        buf.push(ctx, obs(1, text="changed"))
    result = buf.push(ctx, obs(0))
    assert result.status == "delivered"
    assert [event.sequence for event in result.events] == [0, 1]
    assert result.events[1].payload["received_at"] == first.model_dump(mode="json")["received_at"]
    assert buf.push(ctx, obs(0)).status == "duplicate"
    with pytest.raises(ValueError, match="REPLAY_CONFLICT"):
        buf.push(ctx, obs(0, text="changed"))
    assert buf.counts() == {"streams": 1, "pending": 0, "pending_bytes": 0, "recent": 2}


def test_timeout_reports_gaps_without_inventing_events():
    clock = SimulatedClock(NOW)
    buf = OrderedObservationBuffer(clock, reorder_seconds=1)
    ctx = context()
    buf.open(ctx)
    assert buf.push(ctx, obs(3)).status == "buffered"
    assert buf.push(ctx, obs(5)).status == "buffered"
    clock.advance(timedelta(milliseconds=900))
    assert buf.flush(ctx).status == "idle"
    clock.advance(timedelta(milliseconds=100))
    result = buf.flush(ctx)
    assert result.status == "gap"
    assert result.gaps == (SequenceGap(0, 0, 2), SequenceGap(0, 4, 4))
    assert [event.sequence for event in result.events] == [3, 5]
    assert buf.push(ctx, obs(1)).status == "late"
    assert buf.push(ctx, obs(4)).status == "late"
    assert buf.push(ctx, obs(6)).events[0].sequence == 6
    assert buf.counts()["pending_bytes"] == 0


def test_pending_and_distance_limits_do_not_overwrite_or_advance():
    clock = SimulatedClock(NOW)
    buf = OrderedObservationBuffer(clock, max_pending_per_stream=1, max_distance=3)
    ctx = context()
    buf.open(ctx)
    assert buf.push(ctx, obs(2)).status == "buffered"
    initial = buf.counts()
    assert buf.push(ctx, obs(3)).status == "pressure"
    assert buf.push(ctx, obs(100)).status == "pressure"
    assert buf.counts() == initial
    assert [e.sequence for e in buf.push(ctx, obs(0)).events] == [0]
    assert [e.sequence for e in buf.push(ctx, obs(1)).events] == [1, 2]
    assert buf.push(ctx, obs(3)).status == "delivered"
    assert buf.counts()["pending"] == 0


def test_total_bytes_and_stream_capacity_and_close():
    clock = SimulatedClock(NOW)
    ctx, other = context(), context(stream_id=UUID(int=88))
    probe = OrderedObservationBuffer(clock)
    probe.open(ctx)
    probe.push(ctx, obs(1))
    size = probe.counts()["pending_bytes"]
    buf = OrderedObservationBuffer(clock, max_streams=2, max_pending_bytes=size)
    buf.open(ctx)
    buf.open(other)
    with pytest.raises(ValueError, match="STREAM_CAPACITY"):
        buf.open(context(stream_id=UUID(int=99)))
    assert buf.push(ctx, obs(1)).status == "buffered"
    assert buf.push(other, obs(1)).status == "pressure"
    assert buf.counts()["pending_bytes"] == size
    assert buf.close(ctx) == 1
    assert buf.push(other, obs(1)).status == "buffered"
    assert buf.close(other) == 1
    assert buf.counts() == {"streams": 0, "pending": 0, "pending_bytes": 0, "recent": 0}


def test_epoch_and_context_changes_fail_closed():
    clock = SimulatedClock(NOW)
    buf = OrderedObservationBuffer(clock)
    ctx = context()
    buf.open(ctx)
    buf.push(ctx, obs(2))
    with pytest.raises(ValueError, match="CONTEXT_MISMATCH"):
        buf.push(context(actor_id="PERSON_OTHER"), obs(0))
    with pytest.raises(ValueError, match="EPOCH_INVALID"):
        buf.reconnect(ctx, context(epoch=2))
    with pytest.raises(ValueError, match="EPOCH_INVALID"):
        buf.reconnect(ctx, context(epoch=1, remote_processing_allowed=True))
    new = context(epoch=1)
    assert buf.reconnect(ctx, new) == 1
    assert buf.counts()["pending_bytes"] == 0
    with pytest.raises(ValueError, match="CONTEXT_MISMATCH"):
        buf.push(ctx, obs(0))
    event = buf.push(new, obs(0)).events[0]
    assert event.payload["epoch"] == 1
    assert buf.close(new) == 0
    with pytest.raises(ValueError, match="STREAM_UNKNOWN"):
        buf.push(new, obs(1))


def test_event_size_and_invalid_model_rejected():
    clock = SimulatedClock(NOW)
    buf = OrderedObservationBuffer(clock, max_event_bytes=64)
    ctx = context()
    buf.open(ctx)
    assert buf.push(ctx, obs(0)).status == "pressure"
    assert buf.counts()["recent"] == 0
    buf = OrderedObservationBuffer(clock)
    buf.open(ctx)
    with pytest.raises(ValueError, match="PERCEPTION_EVENT_INVALID"):
        buf.push(ctx, obs(0).model_copy(update={"sequence": -1}))
    assert buf.counts()["recent"] == 0


def test_long_stream_bounds_and_old_replay():
    clock = SimulatedClock(NOW)
    buf = OrderedObservationBuffer(clock, recent_capacity=16)
    ctx = context()
    buf.open(ctx)
    delivered = 0
    for seq in range(0, 2000, 2):
        assert buf.push(ctx, obs(seq + 1)).status == "buffered"
        result = buf.push(ctx, obs(seq))
        assert [e.sequence for e in result.events] == [seq, seq + 1]
        delivered += len(result.events)
        assert buf.counts()["recent"] <= 16
        assert buf.counts()["pending_bytes"] == 0
    assert delivered == 2000
    assert buf.push(ctx, obs(0)).status == "late"
    assert buf.push(ctx, obs(1999)).status == "duplicate"
    assert buf.counts()["recent"] == 16
