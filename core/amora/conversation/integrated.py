from __future__ import annotations

import os
from datetime import timedelta
from uuid import UUID

from amora.cognition import CognitiveRequest, SpeechSegment
from amora.emotion import InteractionAppraiser
from amora.events import AmoraEvent, PrivacyScope

from .models import SpeechUtterance, TurnDecision, TurnSignal, TurnState, UtteranceKind
from .service import ConversationEngine as BaseConversationEngine


_TRUE_VALUES = {"1", "true", "yes", "on"}
_DIRECT_CONTINUITY_SECONDS = 25


class ConversationEngine(BaseConversationEngine):
    """V1 conversation integration.

    Normal social turns now feed the local Emotion Engine before dialogue context
    is compiled. Remote cognition remains an independent opt-in and is never
    inferred from disclosure scope. Direct-address continuity is bounded by the
    original explicit call and cannot renew itself indefinitely.
    """

    def state(self) -> dict[str, object]:
        state = super().state()
        state.update(
            {
                "last_interaction_appraisal": getattr(self, "_last_interaction_appraisal", None),
                "last_emotion_event_id": getattr(self, "_last_emotion_event_id", None),
                "last_emotion_profile": getattr(self, "_last_emotion_profile", None),
                "last_turn_test_mode": getattr(self, "_last_turn_test_mode", False),
            }
        )
        return state

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
        if (
            addressee_id is not None
            and turn_metadata.get("explicit_surface_target") == addressee_id
        ):
            # A Web 1:1 target must not establish/consume an ambient A15 window.
            continuity_inferred = False
        else:
            addressee_id, continuity_inferred = self._resolve_conversational_addressee(
                speaker_id=speaker_id,
                addressee_id=addressee_id,
            )
        turn_metadata["addressee_inferred_by_continuity"] = continuity_inferred
        self._last_turn_test_mode = bool(turn_metadata.get("test_mode", False))

        event_id = UUID(str(source_event_id)) if source_event_id else None
        await self._appraise_interaction(
            text,
            speaker_id=speaker_id,
            addressee_id=addressee_id,
            source_event_id=event_id,
            privacy_scope=privacy_scope,
            metadata=turn_metadata,
        )

        decision, item = await super().observe_completed_utterance(
            text,
            speaker_id=speaker_id,
            addressee_id=addressee_id,
            source_event_id=source_event_id,
            privacy_scope=privacy_scope,
            remote_processing_allowed=remote_processing_allowed,
            session_id=session_id,
            stt_confidence=stt_confidence,
            metadata=turn_metadata,
        )
        if (
            self.cognition is None
            or item is None
            or not remote_processing_allowed
            or privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}
            or bool(turn_metadata.get("test_mode", False))
            or turn_metadata.get("memory_consolidation") is False
        ):
            return decision, item

        turn_raw = item.metadata.get("cognitive_turn_id")
        if not turn_raw:
            return decision, item
        try:
            turn_id = UUID(str(turn_raw))
        except ValueError:
            return decision, item

        reflection_request = CognitiveRequest(
            text=text,
            speaker_id=speaker_id,
            target_id=addressee_id,
            source_event_id=event_id,
            session_id=session_id,
            privacy_scope=privacy_scope,
            remote_processing_allowed=True,
            turn_id=turn_id,
            metadata={
                **turn_metadata,
                "cognitive_mode": "reflection",
                "stt_confidence": stt_confidence,
                "turn_state": decision.state.value,
                "turn_reason_codes": decision.reason_codes,
            },
        )
        await self.cognition.reflect(reflection_request)
        return decision, item

    async def _appraise_interaction(
        self,
        text: str,
        *,
        speaker_id: str,
        addressee_id: str | None,
        source_event_id: UUID | None,
        privacy_scope: PrivacyScope,
        metadata: dict[str, object],
    ) -> None:
        if privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            return
        # Ambient speech must not change Amora's directed affect just because the
        # microphone heard it. Only a resolved direct social turn is appraised.
        if addressee_id != "ENTITY_AMORA":
            return

        appraiser = getattr(self, "_interaction_appraiser", None)
        if appraiser is None:
            appraiser = InteractionAppraiser()
            self._interaction_appraiser = appraiser

        appraisal = appraiser.appraise_utterance(
            text,
            speaker_id=speaker_id,
            addressee_id=addressee_id,
            source_event_id=source_event_id,
            privacy_scope=privacy_scope,
        )
        # Session-level speaker labels may not be canonical entities. Global mood
        # can still be appraised, but directed affect must only target identities
        # known to the Entity Registry.
        if self.emotion.identities.get(speaker_id) is None:
            appraisal = appraisal.model_copy(update={"target_id": None, "agent_id": None})

        self._last_interaction_appraisal = appraisal.model_dump(mode="json")
        self._last_emotion_profile = appraisal.metadata.get("profile")
        persist = metadata.get("emotion_persistence") is not False
        if bool(metadata.get("test_mode", False)) and "emotion_persistence" not in metadata:
            persist = False

        if not persist:
            preview = self.emotion.appraisal.appraise(appraisal)
            self._last_interaction_appraisal = {
                **self._last_interaction_appraisal,
                "preview": preview.model_dump(mode="json"),
                "persistent": False,
            }
            if preview.emotion is not None:
                metadata["voice_emotion"] = preview.emotion.value
                metadata["voice_emotion_intensity"] = preview.intensity
            self._last_emotion_event_id = None
            return

        result = await self.emotion.process(appraisal)
        self._last_interaction_appraisal = {
            **self._last_interaction_appraisal,
            "result": result.appraisal.model_dump(mode="json"),
            "persistent": True,
        }
        if result.event is not None:
            metadata["voice_emotion"] = result.event.emotion.value
            metadata["voice_emotion_intensity"] = result.event.intensity
        self._last_emotion_event_id = str(result.event.id) if result.event is not None else None

    def _resolve_conversational_addressee(
        self,
        *,
        speaker_id: str,
        addressee_id: str | None,
    ) -> tuple[str | None, bool]:
        """Keep one fixed direct-conversation window after an explicit call.

        Inferred continuity can use the remaining explicit window but never renews
        it. Otherwise ambient speech could bootstrap an effectively permanent
        addressee authorization.
        """
        now = self.clock.now()
        deadlines = getattr(self, "_direct_addressee_deadlines", None)
        if deadlines is None:
            deadlines = {}
            self._direct_addressee_deadlines = deadlines

        if addressee_id == "ENTITY_AMORA":
            deadlines[speaker_id] = now + timedelta(seconds=_DIRECT_CONTINUITY_SECONDS)
            return addressee_id, False

        if addressee_id is not None:
            deadlines.pop(speaker_id, None)
            return addressee_id, False

        deadline = deadlines.get(speaker_id)
        if deadline is None or now > deadline:
            deadlines.pop(speaker_id, None)
            return None, False

        return "ENTITY_AMORA", True

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

        if self.cognition is None or not self._remote_autonomy_allowed(event):
            return await super().handle_semantic_event(event)

        counterparty = target_id or "PERSON_BK"
        request = CognitiveRequest(
            text=(
                f"Intenção autônoma interna: {action_kind}. "
                f"Inicie contato com {counterparty} de forma natural e coerente com o estado canônico. "
                "Isto não é uma fala recebida e não deve ser respondido como se fosse uma mensagem da pessoa."
            ),
            speaker_id=counterparty,
            target_id=target_id,
            source_event_id=event.id,
            privacy_scope=event.privacy_scope,
            remote_processing_allowed=True,
            metadata={
                "cognitive_mode": "autonomous_initiation",
                "action_kind": action_kind,
                "intention_id": event.payload.get("intention_id"),
                "turn_state": decision.state.value,
                "turn_reason_codes": decision.reason_codes,
            },
        )
        self.cognition.observe_input(request)
        self.cognition.record_turn_decision(request, decision.state.value, decision.reason_codes, decision.score)

        first_utterance: SpeechUtterance | None = None
        mood = self.emotion.current_mood()

        async def queue_segment(segment: SpeechSegment) -> None:
            nonlocal first_utterance
            item = await self.enqueue_text(
                segment.speech_text,
                target_id=target_id,
                kind=UtteranceKind.SEMANTIC,
                priority=80 if decision.state == TurnState.INTERRUPT else 65,
                privacy_scope=event.privacy_scope,
                source_event_id=event.id,
                dedupe_key=f"speech:cognitive:{segment.turn_id}:{segment.ordinal}",
                metadata={
                    "turn_state": decision.state.value,
                    "action_kind": action_kind,
                    "cognitive_turn_id": str(segment.turn_id),
                    "cognitive_segment_id": str(segment.id),
                    "cognitive_ordinal": segment.ordinal,
                    "display_text": segment.metadata.get("display_text", segment.display_text),
                    "cues": segment.metadata.get("cues", []),
                    "unsupported_cues": segment.metadata.get("unsupported_cues", []),
                    "unsupported_markers": segment.metadata.get("unsupported_markers", []),
                    "nonverbal_cues": segment.metadata.get("nonverbal_cues", []),
                    "remote_tts_allowed": True,
                    "autonomous": True,
                },
                style=self.composer.style_for_mood(mood),
            )
            if first_utterance is None and item is not None:
                first_utterance = item
            if item is not None and item.status.value == "cancelled":
                self.cognition.cancel_speech_delivery(segment.turn_id, reason="voice_delivery_suppressed")

        fallback = self.composer.semantic_text(action_kind, target_id)
        plan = await self.cognition.respond(request, on_segment=queue_segment, fallback_text=fallback)
        await self.cognition.emit_event(plan)
        return first_utterance

    async def cancel_utterance(
        self,
        utterance_id: UUID | str,
        *,
        reason: str = "barge_in",
        spoken_chars: int | None = None,
        speech_only: bool = False,
    ) -> tuple[SpeechUtterance | None, int]:
        current = self.store.get_utterance(utterance_id)
        if current is None:
            return None, 0
        turn_raw = current.metadata.get("cognitive_turn_id")
        cancelled = 0
        if turn_raw:
            cancelled = self.store.cancel_cognitive_turn(str(turn_raw), reason=reason, speech_only=speech_only)
            if self.cognition is not None:
                if speech_only:
                    self.cognition.cancel_speech_delivery(str(turn_raw), reason=reason)
                else:
                    active_segment_id = None
                    if current.metadata.get("speech_delivery_unit") != "cognitive_turn":
                        active_segment_id = current.metadata.get("cognitive_segment_id")
                    self.cognition.interrupt_turn(
                        str(turn_raw),
                        reason=reason,
                        active_segment_id=active_segment_id,
                        spoken_chars=spoken_chars if active_segment_id is not None else None,
                    )
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
                    payload={
                        "utterance_id": str(item.id),
                        "reason": reason,
                        "cancelled_utterances": cancelled,
                        "spoken_progress_known": spoken_chars is not None,
                        "spoken_chars": spoken_chars,
                        "speech_only": speech_only,
                    },
                    privacy_scope=item.privacy_scope,
                    causation_id=item.source_event_id,
                    dedupe_key=f"conversation.cancelled:{item.id}:{reason}",
                )
            )
        return item, cancelled

    @staticmethod
    def _remote_autonomy_allowed(event: AmoraEvent) -> bool:
        explicit = event.payload.get("remote_processing_allowed")
        if isinstance(explicit, bool):
            return explicit
        return os.getenv("AMORA_REMOTE_COGNITION_ALLOWED", "0").strip().lower() in _TRUE_VALUES
