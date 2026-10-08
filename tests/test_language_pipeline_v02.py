from datetime import datetime, timezone

from amora.audio import SpeakerResult, Transcript
from amora.events import AsyncEventBus, PrivacyScope
from amora.identity import build_initial_registry
from amora.language import LanguageSocialPipeline
from amora.social import AddresseeEngine, SocialSceneTracker
from amora.time import SimulatedClock


class RecordingStore:
    def __init__(self) -> None:
        self.events = []

    def append_event(self, event) -> bool:
        self.events.append(event)
        return True


def make_pipeline():
    clock = SimulatedClock(datetime(2026, 10, 8, 19, 0, tzinfo=timezone.utc))
    store = RecordingStore()
    bus = AsyncEventBus(event_store=store)
    registry = build_initial_registry()
    social = SocialSceneTracker()
    pipeline = LanguageSocialPipeline(
        clock=clock,
        events=bus,
        social=social,
        addressee=AddresseeEngine(registry),
    )
    return pipeline, clock, store, social


async def test_observed_text_updates_social_scene_and_persists_observation() -> None:
    pipeline, clock, store, social = make_pipeline()
    observed = await pipeline.observe_text(
        "Amora, olha isso",
        speaker_id="PERSON_BK",
        session_id="SESSION_X",
    )
    assert observed.addressee.entity_id == "ENTITY_AMORA"
    assert observed.perspective.speaker_id == "PERSON_BK"
    assert observed.perspective.addressee_id == "ENTITY_AMORA"
    assert social.scene.current_speaker == "PERSON_BK"
    assert social.scene.active_addressee == "ENTITY_AMORA"
    assert len(store.events) == 1
    event = store.events[0]
    assert event.timestamp == clock.now()
    assert event.event_type == "language.utterance.observed"
    assert event.actor == "PERSON_BK"
    assert event.target == "ENTITY_AMORA"
    assert event.payload["observation_type"] == "utterance"


async def test_auth_utterance_is_ephemeral_and_not_written_to_event_store() -> None:
    pipeline, _, store, _ = make_pipeline()
    await pipeline.observe_text(
        "Amora, código secreto",
        speaker_id="PERSON_BK",
        privacy_scope=PrivacyScope.AUTH,
    )
    assert store.events == []


async def test_transcript_keeps_stt_and_speaker_provenance() -> None:
    pipeline, _, store, _ = make_pipeline()
    observed = await pipeline.observe_transcript(
        Transcript(
            text="Amora, vem cá",
            provider="groq",
            model="whisper-large-v3-turbo",
            request_id="req_x",
        ),
        SpeakerResult(
            speaker_id="PERSON_BK",
            known_person_id="PERSON_BK",
            diarization_label="A",
            confidence=0.88,
        ),
        session_id="SESSION_X",
    )
    assert observed.addressee.entity_id == "ENTITY_AMORA"
    event = store.events[0]
    assert event.source == "stt:groq"
    assert event.payload["stt_model"] == "whisper-large-v3-turbo"
    assert event.payload["known_person_id"] == "PERSON_BK"
    assert event.confidence == 0.88
