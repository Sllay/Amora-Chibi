from __future__ import annotations

import os
from uuid import UUID

from amora.cognition import CognitiveRequest, SpeechSegment
from amora.events import AmoraEvent, PrivacyScope

from .models import SpeechUtterance, TurnDecision, TurnSignal, TurnState, UtteranceKind
from .service import ConversationEngine as BaseConversationEngine


_TRUE_VALUES = {"1", "true", "yes", "on"}


class ConversationEngine(BaseConversationEngine):
    """V1 conversation integration.

    Verified semantic intentions still pass through the Turn Manager, but their
    wording can now be generated from canonical Amora state. Remote cognition is
    an independent opt-in and is never inferred from the disclosure scope.
    """

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
    ) -> tuple[TurnDecision, SpeechUtterance | None]:
        decision, item = await super().observe_completed_utterance(
            text,
            speaker_id=speaker_id,
            addressee_id=addressee_id,
            source_event_id=source_event_id,
            privacy_scope=privacy_scope,
            remote_processing_allowed=remote_processing_allowed,
            session_id=session_id,
            stt_confidence=stt_confidence,
        )
        if (
            self.cognition is None
            or item is None
            or not remote_processing_allowed
            or privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}
        ):
            return decision, item

        turn_raw = item.metadata.get("cognitive_turn_id")
        if not turn_raw:
            return decision, item
        try:
            turn_id = UUID(str(turn_raw))
        except ValueError:
            return decision, item

        event_id = UUID(str(source_event_id)) if source_event_id else None
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
                "cognitive_mode": "reflection",
                "stt_confidence": stt_confidence,
                "turn_state": decision.state.value,
                "turn_reason_codes": decision.reason_codes,
            },
        )
        await self.cognition.reflect(reflection_request)
        return decision, item

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
                    "nonverbal_cues": segment.metadata.get("nonverbal_cues", []),
                    "remote_tts_allowed": True,
                    "autonomous": True,
                },
                style=self.composer.style_for_mood(mood),
            )
            if first_utterance is None and item is not None:
                first_utterance = item

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
    ) -> tuple[SpeechUtterance | None, int]:
        current = self.store.get_utterance(utterance_id)
        if current is None:
            return None, 0
        turn_raw = current.metadata.get("cognitive_turn_id")
        cancelled = 0
        if turn_raw:
            cancelled = self.store.cancel_cognitive_turn(str(turn_raw), reason=reason)
            if self.cognition is not None:
                self.cognition.interrupt_turn(
                    str(turn_raw),
                    reason=reason,
                    active_segment_id=current.metadata.get("cognitive_segment_id"),
                    spoken_chars=spoken_chars,
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
