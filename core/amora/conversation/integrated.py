from __future__ import annotations

import os

from amora.cognition import CognitiveRequest, SpeechSegment
from amora.events import AmoraEvent, PrivacyScope

from .models import SpeechUtterance, TurnSignal, TurnState, UtteranceKind
from .service import ConversationEngine as BaseConversationEngine


_TRUE_VALUES = {"1", "true", "yes", "on"}


class ConversationEngine(BaseConversationEngine):
    """V1 conversation integration.

    Verified semantic intentions still pass through the Turn Manager, but their
    wording can now be generated from canonical Amora state. Remote cognition is
    an independent opt-in and is never inferred from the disclosure scope.
    """

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

    @staticmethod
    def _remote_autonomy_allowed(event: AmoraEvent) -> bool:
        explicit = event.payload.get("remote_processing_allowed")
        if isinstance(explicit, bool):
            return explicit
        return os.getenv("AMORA_REMOTE_COGNITION_ALLOWED", "0").strip().lower() in _TRUE_VALUES
