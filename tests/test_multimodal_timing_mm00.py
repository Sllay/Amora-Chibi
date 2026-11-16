"""MM.00D.3B.2A — conservative timing and cross-stream correlation."""
from dataclasses import replace
from uuid import UUID

import pytest

from amora.events.multimodal import ObservationSource, SensoryModality
from amora.events.sensory_timing import ClockCalibration, TimeInterval, correlate_capture
from test_multimodal_contract_mm00 import context


CLOCK = UUID(int=111)
SPEECH = context()
SCREEN = context(source=ObservationSource.SCREEN, stream_id=UUID(int=222), actor_id=None)


def calibration(ctx=SPEECH, modality=SensoryModality.SPEECH, **changes):
    return ClockCalibration(**{
        "context": ctx, "receiver_clock_id": CLOCK, "modality": modality,
        "remote_reference": 10.0, "local_reference": TimeInterval(100.0, 100.1),
        "expires_at": 160.0, "drift_ppm": 0.0, **changes,
    })


def estimate(ctx=SPEECH, modality=SensoryModality.SPEECH, start=10.0, end=11.0,
             arrival=130.0, **changes):
    return calibration(ctx, modality, **changes).estimate(
        ctx, remote_start=start, remote_end=end, received_at=arrival, now=140.0)


def test_mapping_drift_and_delayed_arrival_do_not_change_capture():
    a = estimate(start=20, end=21, drift_ppm=1000)
    b = estimate(start=20, end=21, arrival=139, drift_ppm=1000)
    assert a.start.earliest == pytest.approx(109.99)
    assert a.start.latest == pytest.approx(110.11)
    assert a.end.earliest == pytest.approx(110.989)
    assert a.start == b.start and a.end == b.end
    assert a.received_at != b.received_at


def test_calibration_rejects_wrong_epoch_stale_future_and_extrapolation():
    cal = calibration()
    with pytest.raises(ValueError, match="CONTEXT_MISMATCH"):
        cal.estimate(context(epoch=1), remote_start=10, remote_end=11, received_at=130, now=140)
    with pytest.raises(ValueError, match="CLOCK_EXPIRED"):
        cal.estimate(SPEECH, remote_start=10, remote_end=11, received_at=130, now=160)
    with pytest.raises(ValueError, match="AFTER_ARRIVAL"):
        cal.estimate(SPEECH, remote_start=40, remote_end=41, received_at=120, now=140)
    with pytest.raises(ValueError, match="EXTRAPOLATION"):
        cal.estimate(SPEECH, remote_start=400, remote_end=401, received_at=130, now=140)
    with pytest.raises(ValueError, match="TIME_INVALID"):
        cal.estimate(SPEECH, remote_start=12, remote_end=11, received_at=130, now=140)
    with pytest.raises(ValueError, match="TIME_INVALID"):
        cal.estimate(SPEECH, remote_start=10, remote_end=11, received_at=141, now=140)


@pytest.mark.parametrize("changes", [
    {"remote_reference": float("nan")}, {"expires_at": 500}, {"drift_ppm": -1},
    {"local_reference": TimeInterval(100, 111)}, {"modality": SensoryModality.SCREEN},
])
def test_calibration_bounds(changes):
    with pytest.raises(ValueError, match="CALIBRATION_INVALID"):
        calibration(**changes)


def test_conservative_temporal_match():
    speech = estimate()
    def visual(start, end, **kwargs):
        return estimate(SCREEN, SensoryModality.SCREEN, start, end, **kwargs)
    match = correlate_capture(speech, visual(11.5, 12), recipient_id="PERSON_BK")
    assert match.status == "within_window"
    assert match.minimum_distance == pytest.approx(.4)
    assert match.maximum_distance == pytest.approx(.6)
    match = correlate_capture(speech, visual(13, 14), recipient_id="PERSON_BK")
    assert match.status == "possible"
    assert correlate_capture(speech, visual(14, 15), recipient_id="PERSON_BK").status == "outside_window"
    uncertain = visual(11, 12, local_reference=TimeInterval(99, 101))
    assert correlate_capture(speech, uncertain, recipient_id="PERSON_BK").status == "uncertain"


def test_scope_and_receiver_clock_isolation():
    speech = estimate()
    visual = estimate(SCREEN, SensoryModality.SCREEN)
    for changes in (
        {"session_id": "other-session"},
        {"correlation_id": UUID(int=777)},
        {"stream_id": SPEECH.stream_id},
    ):
        other = SCREEN.model_copy(update=changes)
        with pytest.raises(ValueError, match="CONTEXT_MISMATCH"):
            correlate_capture(speech, estimate(other, SensoryModality.SCREEN), recipient_id="PERSON_BK")
    with pytest.raises(ValueError, match="CONTEXT_MISMATCH"):
        correlate_capture(speech, replace(visual, receiver_clock_id=UUID(int=999)), recipient_id="PERSON_BK")
    with pytest.raises(ValueError, match="FORBIDDEN"):
        correlate_capture(speech, visual, recipient_id="PERSON_OTHER")
    with pytest.raises(ValueError, match="POLICY_INVALID"):
        correlate_capture(visual, speech, recipient_id="PERSON_BK")


def test_invalid_intervals():
    for a, b in ((-1, 0), (2, 1), (0, float("inf")), (True, 1)):
        with pytest.raises(ValueError, match="INTERVAL_INVALID"):
            TimeInterval(a, b)
