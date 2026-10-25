from __future__ import annotations

from pydantic import BaseModel

from amora.audio import SpeakerResult, Transcript
from amora.events import AmoraEvent, AsyncEventBus, PrivacyScope
from amora.social import (
    AddresseeEngine,
    AddresseeKind,
    AddresseeResult,
    SocialSceneTracker,
)
from amora.time import Clock
from .perspective import PerspectiveFrame


class UtteranceObservation(BaseModel):
    text: str | None
    speaker_id: str
    addressee: AddresseeResult
    perspective: PerspectiveFrame
    event_id: str


class LanguageSocialPipeline:
    """Turns text/STT output into social observations, not semantic facts."""

    def __init__(
        self,
        *,
        clock: Clock,
        events: AsyncEventBus,
        social: SocialSceneTracker,
        addressee: AddresseeEngine,
    ) -> None:
        self.clock = clock
        self.events = events
        self.social = social
        self.addressee = addressee

    async def observe_text(
        self,
        text: str,
        *,
        speaker_id: str,
        session_id: str | None = None,
        source: str = "text",
        privacy_scope: PrivacyScope = PrivacyScope.PERSONAL,
        dedupe_key: str | None = None,
        speaker_confidence: float = 1.0,
        extra_payload: dict[str, object] | None = None,
        explicit_addressee_id: str | None = None,
    ) -> UtteranceObservation:
        if explicit_addressee_id is not None:
            if self.addressee.registry.get(explicit_addressee_id) is None:
                raise ValueError("unknown explicit surface addressee")
            result = AddresseeResult(
                kind=AddresseeKind.AMORA
                if explicit_addressee_id == self.addressee.amora_id
                else AddresseeKind.PERSON,
                entity_id=explicit_addressee_id,
                confidence=1.0,
                candidates=[explicit_addressee_id],
                reason_codes=["EXPLICIT_SURFACE_TARGET"],
            )
        else:
            result = self.addressee.infer(
                text,
                speaker_id=speaker_id,
                scene=self.social.scene,
            )
        scene_confidence = min(
            speaker_confidence,
            result.confidence if result.entity_id else speaker_confidence,
        )
        # A private surface has its own target, not the physical/ambient floor.
        if explicit_addressee_id is None:
            self.social.begin_turn(
                speaker_id,
                addressee_id=result.entity_id,
                confidence=scene_confidence,
            )

        is_auth = privacy_scope == PrivacyScope.AUTH
        payload: dict[str, object] = {
            "observation_type": "utterance",
            "addressee_kind": result.kind.value,
            "addressee_confidence": result.confidence,
            "addressee_reason_codes": result.reason_codes,
        }
        if explicit_addressee_id is not None:
            payload["explicit_addressee_id"] = explicit_addressee_id
        if is_auth:
            payload["auth_redacted"] = True
        else:
            payload["text"] = text
        if extra_payload:
            payload.update(extra_payload)

        event = AmoraEvent(
            event_type="language.utterance.observed",
            timestamp=self.clock.now(),
            source=source,
            actor=speaker_id,
            target=result.entity_id,
            confidence=min(speaker_confidence, result.confidence),
            payload=payload,
            privacy_scope=privacy_scope,
            session_id=session_id,
            dedupe_key=dedupe_key,
        )

        # AUTH content never enters durable state. Its raw text is also omitted
        # from the event payload so subscribers cannot accidentally log/forward it.
        published = await self.events.publish(event, persist=not is_auth)
        return UtteranceObservation(
            text=None if is_auth else text,
            speaker_id=speaker_id,
            addressee=result,
            perspective=PerspectiveFrame(
                speaker_id=speaker_id,
                addressee_id=result.entity_id,
            ),
            event_id=published.event_id,
        )

    async def observe_transcript(
        self,
        transcript: Transcript,
        speaker: SpeakerResult,
        *,
        session_id: str | None = None,
        privacy_scope: PrivacyScope = PrivacyScope.PERSONAL,
        dedupe_key: str | None = None,
        explicit_addressee_id: str | None = None,
    ) -> UtteranceObservation:
        return await self.observe_text(
            transcript.text,
            speaker_id=speaker.speaker_id,
            session_id=session_id,
            source=f"stt:{transcript.provider}",
            privacy_scope=privacy_scope,
            dedupe_key=dedupe_key,
            speaker_confidence=speaker.confidence,
            explicit_addressee_id=explicit_addressee_id,
            extra_payload={
                "stt_provider": transcript.provider,
                "stt_model": transcript.model,
                "stt_request_id": transcript.request_id,
                "diarization_label": speaker.diarization_label,
                "known_person_id": speaker.known_person_id,
            },
        )
