"""MM.00C: contract and existing-bus compatibility, no live adapters."""
import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from pydantic import ValidationError

from amora.events import AmoraEvent, AsyncEventBus, PrivacyScope
from amora.events.multimodal import (
    EvidenceKind, ObservationContext, ObservationSource, SensoryModality,
    SensoryObservation, UnknownReason, build_observation_event,
)
from amora.storage import AmoraDatabase


NOW = datetime(2026, 11, 16, tzinfo=timezone.utc)
SCOPE = UUID(int=1)
STREAM = UUID(int=2)
CORRELATION = UUID(int=3)


def context(**overrides):
    return ObservationContext(**{
        "source": ObservationSource.DISCORD, "session_id": "session-1",
        "scope_id": SCOPE, "stream_id": STREAM, "correlation_id": CORRELATION,
        "epoch": 0, "actor_id": "PERSON_BK", "owner_entity_id": "PERSON_BK",
        **overrides,
    })


def observation(**overrides):
    return SensoryObservation(**{
        "modality": SensoryModality.SPEECH, "evidence_kind": EvidenceKind.REPORTED,
        "sequence": 12, "capture_started_at": NOW,
        "capture_ended_at": NOW + timedelta(seconds=1),
        "received_at": NOW + timedelta(seconds=2), "confidence": .95,
        "text": "O coelho branco fugiu perto do rio.",
        "provider": "groq", "model": "whisper-large-v3-turbo",
        **overrides,
    })


def test_speech_event_reuses_canonical_envelope_and_provenance():
    obs = observation()
    ctx = context(audience=("PERSON_LUCAS", "PERSON_BK"))
    event = build_observation_event(ctx, obs)
    assert event.event_type == "perception.observation"
    assert event.actor == "PERSON_BK"
    assert event.timestamp == obs.capture_ended_at
    assert event.session_id == "session-1"
    assert event.correlation_id == CORRELATION
    assert event.sequence == 12
    assert event.privacy_scope == PrivacyScope.PERSONAL
    assert event.payload["evidence_kind"] == "reported"
    assert event.payload["remote_processing_allowed"] is False
    assert event.payload["scope_id"] == str(SCOPE)
    assert event.payload["audience"] == ["PERSON_BK", "PERSON_LUCAS"]
    assert AmoraEvent.model_validate_json(event.model_dump_json()) == event


def test_dedupe_coordinates_do_not_depend_on_arrival_or_semantic_content():
    ctx, obs = context(), observation()
    event = build_observation_event(ctx, obs)
    replay = observation(received_at=NOW + timedelta(seconds=9))
    assert build_observation_event(ctx, replay).dedupe_key == event.dedupe_key
    # Same delivery ID with different semantics is a conflict, not a new event.
    assert build_observation_event(ctx, observation(text="another")).id == event.id
    for other in (
        context(session_id="session-2"),
        context(scope_id=UUID(int=4)),
        context(stream_id=UUID(int=5)),
        context(epoch=1),
        context(source=ObservationSource.WEB),
    ):
        assert build_observation_event(other, obs).id != event.id
    assert build_observation_event(ctx, observation(sequence=13)).id != event.id


@pytest.mark.parametrize("fields", [
    {"text": b"audio"},
    {"raw_audio": b"secret"},
    {"frame": b"image"},
    {"metadata": {"token": "secret"}},
    {"actor_id": "PERSON_OTHER"},
    {"text": " "},
    {"text": "x" * 4097},
    {"capture_started_at": NOW.replace(tzinfo=None)},
    {"capture_ended_at": NOW - timedelta(seconds=1)},
    {"received_at": NOW.replace(tzinfo=None)},
    {"sequence": -1},
    {"sequence": True},
    {"confidence": 1.1},
    {"provider": None},
    {"content_sha256": "invalid"},
    {"evidence_kind": EvidenceKind.OBSERVED},
    {"unknown_reason": UnknownReason.GAP},
])
def test_rejects_untrusted_or_invalid_observation(fields):
    with pytest.raises(ValidationError):
        observation(**fields)


@pytest.mark.parametrize("fields", [
    {"owner_entity_id": None},
    {"audience": ("PERSON_BK", "PERSON_BK")},
    {"privacy_scope": PrivacyScope.PRIVATE, "remote_processing_allowed": True},
    {"privacy_scope": PrivacyScope.SECRET, "remote_processing_allowed": True},
    {"privacy_scope": PrivacyScope.AUTH, "remote_processing_allowed": True},
    {"remote_processing_allowed": "true"},
    {"epoch": True},
    {"session_id": " "},
])
def test_context_fails_closed(fields):
    with pytest.raises(ValidationError):
        context(**fields)


def test_unknown_speaker_and_unknown_gap_do_not_invent_speech():
    with pytest.raises(ValueError, match="resolved speaker"):
        build_observation_event(context(actor_id=None), observation())
    gap = observation(evidence_kind=EvidenceKind.UNKNOWN, text=None,
                      unknown_reason=UnknownReason.GAP, provider=None, model=None)
    event = build_observation_event(context(actor_id=None), gap)
    assert event.actor is None
    assert event.payload["unknown_reason"] == "gap"
    assert "text" not in event.payload
    with pytest.raises(ValidationError):
        observation(evidence_kind=EvidenceKind.UNKNOWN, unknown_reason=UnknownReason.GAP)


def test_screen_and_image_evidence_are_not_user_assertions():
    screen = observation(modality=SensoryModality.SCREEN, evidence_kind=EvidenceKind.INFERRED,
                         text="Possível animal próximo ao rio.", provider="vision", model="test")
    with pytest.raises(ValueError, match="authorized screen"):
        build_observation_event(context(), screen)
    ctx = context(source=ObservationSource.SCREEN, actor_id=None)
    event = build_observation_event(ctx, screen)
    assert event.actor is None
    assert event.payload["evidence_kind"] == "inferred"
    assert event.source == "perception:screen"
    with pytest.raises(ValueError, match="screen stream"):
        build_observation_event(ctx, observation())
    with pytest.raises(ValidationError):
        observation(modality=SensoryModality.IMAGE)
    image = observation(modality=SensoryModality.IMAGE, evidence_kind=EvidenceKind.OBSERVED)
    assert build_observation_event(context(), image).payload["modality"] == "image"


@pytest.mark.parametrize("scope", [PrivacyScope.AUTH, PrivacyScope.SECRET])
def test_restricted_content_never_builds_event(scope):
    with pytest.raises(ValueError, match="restricted content"):
        build_observation_event(context(privacy_scope=scope), observation())


def test_existing_bus_persists_selected_event_once_across_restart(tmp_path):
    path = tmp_path / "events.db"
    event = build_observation_event(context(), observation())

    async def publish(db, event):
        return await AsyncEventBus(event_store=db).publish(event)

    db = AmoraDatabase(path)
    try:
        db.migrate()
        assert not asyncio.run(publish(db, event)).deduplicated
        assert db.event_count() == 1
    finally:
        db.close()

    db = AmoraDatabase(path)
    try:
        replay = build_observation_event(context(), observation(received_at=NOW + timedelta(seconds=20)))
        assert asyncio.run(publish(db, replay)).deduplicated
        assert db.event_count() == 1
        # Raw samples are not passed to the durable store; explicit ephemeral
        # publication uses the existing bus contract.
        ephemeral = build_observation_event(context(), observation(sequence=13))
        asyncio.run(AsyncEventBus(event_store=db).publish(ephemeral, persist=False))
        assert db.event_count() == 1
    finally:
        db.close()
