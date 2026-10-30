from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

from amora.emotion import EmotionEngine
from amora.events import AmoraEvent, AsyncEventBus, PrivacyScope
from amora.social import SocialSceneTracker
from amora.time import Clock

from .composer import LocalSpeechComposer
from .dialogue import DialogueProvider, NoDialogueProvider
from .models import SpeechAck, SpeechUtterance, TurnDecision, TurnSignal, TurnState, UtteranceKind
from .store import ConversationStore
from .turn_manager import TurnManager

if TYPE_CHECKING:
    from amora.cognition import CognitiveEngine, CognitiveRequest, SpeechSegment


class ConversationEngine:
    def __init__(
        self,
        store: ConversationStore,
        clock: Clock,
        social: SocialSceneTracker,
        emotion: EmotionEngine,
        events: AsyncEventBus,
        *,
        dialogue: DialogueProvider | None = None,
        cognition: "CognitiveEngine | None" = None,
    ) -> None:
        self.store = store
        self.clock = clock
        self.social = social
        self.emotion = emotion
        self.events = events
        self.turn_manager = TurnManager(clock)
        self.composer = LocalSpeechComposer()
        self.dialogue = dialogue or NoDialogueProvider()
        self.cognition = cognition

    def close(self) -> None:
        self.store.close()

    def state(self) -> dict[str, object]:
        return {
            "turn_decision_count": self.store.turn_count(),
            "speech_pending_count": self.store.pending_count(),
            "speech_spoken_count": self.store.spoken_count(),
            "dialogue_provider": self.dialogue.name,
            "cognition_enabled": self.cognition is not None,
            "conversation_floor": self.social.scene.conversation_floor,
            "active_addressee": self.social.scene.active_addressee,
        }

    def decide_turn(self, signal: TurnSignal) -> TurnDecision:
        if signal.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            redacted = signal.model_copy(update={"partial_text": None, "dedupe_key": None})
            return self.turn_manager.decide(redacted)
        if signal.dedupe_key:
            existing = self.store.get_turn_by_dedupe(signal.dedupe_key)
            if existing is not None:
                return existing
        decision = self.turn_manager.decide(signal)
        if not self.store.put_turn_decision(decision) and signal.dedupe_key:
            replay = self.store.get_turn_by_dedupe(signal.dedupe_key)
            if replay is not None:
                return replay
        return decision

    async def handle_semantic_event(self, event: AmoraEvent) -> SpeechUtterance | None:
        if event.event_type != "action.semantic_request":
            return None
        if event.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            return None
        action_kind = str(event.payload.get("action_kind") or "unknown")
        target_id = event.target
        floor = self.social.scene.conversation_floor
        current_speaker = self.social.scene.current_speaker
        signal = TurnSignal(
            speaker_id=None,
            addressee_id=target_id,
            conversation_floor=floor,
            vad_active=current_speaker not in {None, "ENTITY_AMORA"},
            pause_ms=500 if current_speaker is None else 0,
            direct_call=False,
            speech_requested=True,
            active_conversation=bool(self.social.scene.participants),
            can_interrupt=True,
            urgency=max(0.0, min(1.0, event.importance)),
            privacy_scope=event.privacy_scope,
            source_event_id=event.id,
            dedupe_key=f"turn:semantic:{event.id}",
        )
        decision = self.decide_turn(signal)
        if decision.state not in {TurnState.TAKE_TURN, TurnState.INTERRUPT}:
            return None
        mood = self.emotion.current_mood()
        text = self.composer.semantic_text(action_kind, target_id)
        return await self.enqueue_text(
            text,
            target_id=target_id,
            kind=UtteranceKind.SEMANTIC,
            priority=80 if decision.state == TurnState.INTERRUPT else 65,
            privacy_scope=event.privacy_scope,
            source_event_id=event.id,
            dedupe_key=f"speech:semantic:{event.id}",
            metadata={"turn_state": decision.state.value, "action_kind": action_kind},
            style=self.composer.style_for_mood(mood),
        )

    async def observe_completed_utterance(
        self,
        text: str,
        *,
        speaker_id: str,
        addressee_id: str | None,
        source_event_id: UUID | str | None,
        privacy_scope: PrivacyScope,
        remote_processing_allowed: bool = False,
        session_id: str | None = None,
        stt_confidence: float | None = None,
        metadata: dict[str, object] | None = None,
    ) -> tuple[TurnDecision, SpeechUtterance | None]:
        turn_metadata = dict(metadata or {})
        # Platform is transport, not identity. Callers may explicitly target a
        # speech adapter; legacy/non-platform callers remain on Android by default.
        speech_device_id = str(turn_metadata.get("speech_device_id") or "DEVICE_A15")
        direct = addressee_id == "ENTITY_AMORA"
        event_id = UUID(str(source_event_id)) if source_event_id else None
        signal = TurnSignal(
            speaker_id=speaker_id,
            addressee_id=addressee_id,
            conversation_floor=None,
            vad_active=False,
            pause_ms=450,
            partial_text=None if privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET} else text[:240],
            direct_call=direct,
            speech_requested=False,
            active_conversation=True,
            can_interrupt=True,
            urgency=0.25,
            privacy_scope=privacy_scope,
            source_event_id=event_id,
            dedupe_key=f"turn:utterance:{event_id}" if event_id else None,
        )
        decision = self.decide_turn(signal)

        cognitive_request = None
        if self.cognition is not None:
            from amora.cognition import CognitiveRequest

            cognitive_request = CognitiveRequest(
                text=text,
                speaker_id=speaker_id,
                target_id=addressee_id,
                source_event_id=event_id,
                session_id=session_id,
                privacy_scope=privacy_scope,
                remote_processing_allowed=remote_processing_allowed,
                metadata={
                    **turn_metadata,
                    "stt_confidence": stt_confidence,
                    "turn_state": decision.state.value,
                    "turn_reason_codes": decision.reason_codes,
                },
            )
            self.cognition.observe_input(cognitive_request)
            self.cognition.record_turn_decision(cognitive_request, decision.state.value, decision.reason_codes, decision.score)

        if decision.state != TurnState.TAKE_TURN or not direct:
            return decision, None
        if privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            return decision, None

        if self.cognition is not None and cognitive_request is not None:
            first_utterance: SpeechUtterance | None = None
            mood = self.emotion.current_mood()
            style = self._style_for_turn(mood, turn_metadata)
            remote_tts_allowed = remote_processing_allowed and privacy_scope in {PrivacyScope.PUBLIC, PrivacyScope.PERSONAL}

            # Safari cloud TTS is a turn-level stream. Cognitive segmentation is
            # retained for journal/history but no longer creates one Fish request
            # per clause. The Web request already waits for respond() to finish, so
            # the complete SpeechPlan is available before the browser claims voice.
            if speech_device_id == "WEB_SAFARI":
                fallback = self.composer.fallback_dialogue(text, speaker_id=speaker_id)
                plan = await self.cognition.respond(cognitive_request, on_segment=None, fallback_text=fallback)
                first_utterance = await self._enqueue_web_cognitive_turn(
                    plan,
                    target_id=speaker_id,
                    privacy_scope=privacy_scope,
                    source_event_id=event_id,
                    remote_tts_allowed=remote_tts_allowed,
                    turn_state=decision.state.value,
                    turn_metadata=turn_metadata,
                    style=style,
                )
                await self.cognition.emit_event(plan)
                return decision, first_utterance

            async def queue_segment(segment: "SpeechSegment") -> None:
                nonlocal first_utterance
                item = await self.enqueue_text(
                    segment.speech_text,
                    target_id=speaker_id,
                    kind=UtteranceKind.DIALOGUE,
                    priority=70,
                    privacy_scope=privacy_scope,
                    source_event_id=event_id,
                    dedupe_key=f"speech:cognitive:{segment.turn_id}:{segment.ordinal}",
                    device_id=speech_device_id,
                    metadata={
                        **_execution_metadata(turn_metadata),
                        "turn_state": decision.state.value,
                        "cognitive_turn_id": str(segment.turn_id),
                        "cognitive_segment_id": str(segment.id),
                        "cognitive_ordinal": segment.ordinal,
                        "display_text": segment.metadata.get("display_text", segment.display_text),
                        "cues": segment.metadata.get("cues", []),
                        "unsupported_cues": segment.metadata.get("unsupported_cues", []),
                        "unsupported_markers": segment.metadata.get("unsupported_markers", []),
                        "nonverbal_cues": segment.metadata.get("nonverbal_cues", []),
                        "remote_tts_allowed": remote_tts_allowed,
                    },
                    style=style,
                )
                if first_utterance is None and item is not None:
                    first_utterance = item
                if item is not None and item.status.value == "cancelled":
                    self.cognition.cancel_speech_delivery(segment.turn_id, reason="voice_delivery_suppressed")

            fallback = self.composer.fallback_dialogue(text, speaker_id=speaker_id)
            plan = await self.cognition.respond(cognitive_request, on_segment=queue_segment, fallback_text=fallback)
            await self.cognition.emit_event(plan)
            return decision, first_utterance

        reply: str
        if remote_processing_allowed and self.dialogue.name != "none":
            try:
                reply = await self.dialogue.reply(text, speaker_id=speaker_id, target_id=addressee_id)
            except Exception:
                reply = self.composer.fallback_dialogue(text, speaker_id=speaker_id)
        else:
            reply = self.composer.fallback_dialogue(text, speaker_id=speaker_id)
        mood = self.emotion.current_mood()
        utterance = await self.enqueue_text(
            reply,
            target_id=speaker_id,
            kind=UtteranceKind.DIALOGUE,
            priority=70,
            privacy_scope=privacy_scope,
            source_event_id=event_id,
            dedupe_key=f"speech:utterance:{event_id}" if event_id else None,
            device_id=speech_device_id,
            metadata={
                **_execution_metadata(turn_metadata),
                "turn_state": decision.state.value,
                "remote_provider_used": remote_processing_allowed and self.dialogue.name != "none",
            },
            style=self._style_for_turn(mood, turn_metadata),
        )
        return decision, utterance

    async def _enqueue_web_cognitive_turn(
        self,
        plan,
        *,
        target_id: str,
        privacy_scope: PrivacyScope,
        source_event_id: UUID | None,
        remote_tts_allowed: bool,
        turn_state: str,
        turn_metadata: dict[str, object],
        style,
    ) -> SpeechUtterance | None:
        assert self.cognition is not None
        segments = self.cognition.store.list_segments(plan.turn_id)
        segment_ids = [str(segment.id) for segment in segments]
        metadata: dict[str, object] = {
            **_execution_metadata(turn_metadata),
            "turn_state": turn_state,
            "cognitive_turn_id": str(plan.turn_id),
            "cognitive_segment_ids": segment_ids,
            "cognitive_segment_id": segment_ids[0] if segment_ids else None,
            "display_text": plan.display_text,
            "cues": _merge_segment_values(segments, "cues"),
            "unsupported_cues": _merge_segment_values(segments, "unsupported_cues"),
            "unsupported_markers": _merge_segment_values(segments, "unsupported_markers"),
            "nonverbal_cues": _merge_segment_values(segments, "nonverbal_cues"),
            "remote_tts_allowed": remote_tts_allowed,
            "speech_delivery_unit": "cognitive_turn",
        }
        return await self.enqueue_text(
            plan.speech_text,
            target_id=target_id,
            kind=UtteranceKind.DIALOGUE,
            priority=70,
            privacy_scope=privacy_scope,
            source_event_id=source_event_id,
            dedupe_key=f"speech:cognitive-turn:{plan.turn_id}",
            device_id="WEB_SAFARI",
            metadata=metadata,
            style=style,
        )

    def _style_for_turn(self, mood, metadata: dict[str, object]):
        raw_intensity = metadata.get("voice_emotion_intensity", 0.0)
        try:
            intensity = float(raw_intensity)
        except (TypeError, ValueError):
            intensity = 0.0
        return self.composer.style_for_mood(
            mood,
            emotion=metadata.get("voice_emotion"),
            intensity=intensity,
        )

    async def enqueue_backchannel(
        self,
        signal: TurnSignal,
        *,
        dedupe_key: str | None = None,
    ) -> tuple[TurnDecision, SpeechUtterance | None]:
        if dedupe_key:
            signal.dedupe_key = dedupe_key
        decision = self.decide_turn(signal)
        if decision.state != TurnState.BACKCHANNEL:
            return decision, None
        mood = self.emotion.current_mood()
        item = await self.enqueue_text(
            self.composer.backchannel_text(signal.partial_text),
            target_id=signal.speaker_id,
            kind=UtteranceKind.BACKCHANNEL,
            priority=45,
            privacy_scope=signal.privacy_scope,
            source_event_id=signal.source_event_id,
            dedupe_key=f"speech:{dedupe_key}" if dedupe_key else None,
            metadata={"turn_state": TurnState.BACKCHANNEL.value},
            style=self.composer.style_for_mood(mood),
        )
        return decision, item

    async def enqueue_text(
        self,
        text: str,
        *,
        target_id: str | None,
        kind: UtteranceKind,
        priority: int,
        privacy_scope: PrivacyScope,
        source_event_id: UUID | None,
        dedupe_key: str | None,
        device_id: str = "DEVICE_A15",
        metadata: dict[str, object] | None = None,
        style=None,
    ) -> SpeechUtterance | None:
        if privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            return None
        if dedupe_key:
            existing = self.store.get_utterance_by_dedupe(dedupe_key)
            if existing is not None:
                return existing
        item = SpeechUtterance(
            text=text,
            display_text=str((metadata or {}).get("display_text", text)),
            target_id=target_id,
            kind=kind,
            priority=priority,
            style=style or self.composer.style_for_mood(self.emotion.current_mood()),
            privacy_scope=privacy_scope,
            source_event_id=source_event_id,
            device_id=device_id,
            created_at=self.clock.now(),
            dedupe_key=dedupe_key,
            metadata=metadata or {},
        )
        if not self.store.enqueue(item):
            if dedupe_key:
                return self.store.get_utterance_by_dedupe(dedupe_key)
            return None
        await self.events.publish(
            AmoraEvent(
                event_type="conversation.utterance_queued",
                timestamp=self.clock.now(),
                source="conversation_engine",
                actor="ENTITY_AMORA",
                target=target_id,
                importance=0.6,
                payload={"utterance_id": str(item.id), "kind": item.kind.value, "device_id": item.device_id},
                privacy_scope=privacy_scope,
                causation_id=source_event_id,
                dedupe_key=f"conversation.queued:{item.id}",
            )
        )
        return item

    def claim_next(
        self,
        device_id: str = "DEVICE_A15",
        *,
        cognitive_turn_id: UUID | str | None = None,
    ) -> SpeechUtterance | None:
        # Claiming means the client reserved work, not that sound is already being
        # emitted. This distinction lets Android pre-render N+1 without falsifying
        # the Cognitive Journal's TTS_STARTED stage.
        item = self.store.claim_next(device_id, cognitive_turn_id=cognitive_turn_id)
        # Pre-upgrade utterances have only segment text in their JSON. Reconstruct
        # one display snapshot from stored *display* fragments, never speech_text.
        if item is not None and item.display_text is None:
            turn = item.metadata.get("cognitive_turn_id")
            if turn and self.cognition is not None:
                segments = self.cognition.store.list_segments(str(turn))
                item.display_text = " ".join(segment.display_text for segment in segments) or item.text
            else:
                item.display_text = item.text
        return item

    def mark_utterance_speaking(self, utterance_id: UUID | str) -> SpeechUtterance | None:
        item = self.store.get_utterance(utterance_id)
        if item is None:
            return None
        if item.status.value in {"cancelled", "spoken"}:
            return item
        if self.cognition is not None:
            turn_id = item.metadata.get("cognitive_turn_id")
            if turn_id:
                for segment_id in _cognitive_segment_ids(item):
                    self.cognition.mark_segment_speaking(segment_id, turn_id=str(turn_id))
        return item

    async def acknowledge(self, utterance_id: UUID | str, ack: SpeechAck) -> SpeechUtterance | None:
        item = self.store.acknowledge(utterance_id, spoken=ack.spoken)
        if item is None:
            return None
        if item.status.value == "cancelled":
            return item
        if self.cognition is not None:
            turn_id = item.metadata.get("cognitive_turn_id")
            if turn_id:
                for segment_id in _cognitive_segment_ids(item):
                    self.cognition.acknowledge_segment(segment_id, spoken=ack.spoken, turn_id=str(turn_id))
        if ack.spoken:
            await self.events.publish(
                AmoraEvent(
                    event_type="conversation.utterance_spoken",
                    timestamp=self.clock.now(),
                    source="conversation_engine",
                    actor="ENTITY_AMORA",
                    target=item.target_id,
                    importance=0.55,
                    payload={"utterance_id": str(item.id), "kind": item.kind.value},
                    privacy_scope=item.privacy_scope,
                    causation_id=item.source_event_id,
                    dedupe_key=f"conversation.spoken:{item.id}",
                )
            )
        return item

    async def cancel_utterance(self, utterance_id: UUID | str, *, reason: str = "barge_in") -> tuple[SpeechUtterance | None, int]:
        current = self.store.get_utterance(utterance_id)
        if current is None:
            return None, 0
        turn_raw = current.metadata.get("cognitive_turn_id")
        cancelled = 0
        if turn_raw:
            cancelled = self.store.cancel_cognitive_turn(str(turn_raw), reason=reason)
            if self.cognition is not None:
                self.cognition.interrupt_turn(str(turn_raw), reason=reason)
            item = self.store.get_utterance(utterance_id)
        else:
            item = self.store.cancel(utterance_id, reason=reason)
            cancelled = 1 if item is not None else 0
        if item is not None:
            await self.events.publish(
                AmoraEvent(
                    event_type="conversation.utterance_cancelled",
                    timestamp=self.clock.now(),
                    source="conversation_engine",
                    actor="ENTITY_AMORA",
                    target=item.target_id,
                    importance=0.45,
                    payload={"utterance_id": str(item.id), "reason": reason, "cancelled_utterances": cancelled},
                    privacy_scope=item.privacy_scope,
                    causation_id=item.source_event_id,
                    dedupe_key=f"conversation.cancelled:{item.id}:{reason}",
                )
            )
        return item, cancelled


def _merge_segment_values(segments, key: str) -> list[str]:
    values: list[str] = []
    for segment in segments:
        raw = segment.metadata.get(key, [])
        if not isinstance(raw, list):
            continue
        for value in raw:
            text = str(value)
            if text and text not in values:
                values.append(text)
    return values


def _cognitive_segment_ids(item: SpeechUtterance) -> list[str]:
    raw = item.metadata.get("cognitive_segment_ids")
    if isinstance(raw, list):
        values = [str(value) for value in raw if value]
        if values:
            return values
    single = item.metadata.get("cognitive_segment_id")
    return [str(single)] if single else []


def _execution_metadata(metadata: dict[str, object]) -> dict[str, object]:
    keys = (
        "test_mode",
        "memory_consolidation",
        "relationship_learning",
        "emotion_persistence",
        "voice_emotion",
        "voice_emotion_intensity",
    )
    return {key: metadata[key] for key in keys if key in metadata}
