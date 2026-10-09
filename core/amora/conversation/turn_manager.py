from __future__ import annotations

from amora.events import PrivacyScope
from amora.time import Clock

from .models import TurnDecision, TurnSignal, TurnState


class TurnManager:
    """Deterministic social turn arbitration for V0.8.

    The manager never decides semantic content. It only decides whether Amora should
    listen, wait, backchannel, take the floor, interrupt, or yield.
    """

    def __init__(self, clock: Clock) -> None:
        self.clock = clock

    def decide(self, signal: TurnSignal) -> TurnDecision:
        state, reasons, score = self._decide(signal)
        return TurnDecision(
            state=state,
            reason_codes=reasons,
            score=score,
            signal=signal,
            created_at=self.clock.now(),
            dedupe_key=signal.dedupe_key,
        )

    def _decide(self, signal: TurnSignal) -> tuple[TurnState, list[str], float]:
        if signal.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            return TurnState.WAIT, ["PRIVACY_TTS_DENIED"], 1.0

        floor = signal.conversation_floor
        other_has_floor = floor not in {None, "ENTITY_AMORA"}
        directed_to_amora = signal.addressee_id == "ENTITY_AMORA" or signal.direct_call

        if signal.vad_active and other_has_floor:
            if (
                signal.speech_requested
                and signal.can_interrupt
                and signal.urgency >= 0.85
            ):
                return TurnState.INTERRUPT, ["URGENT_REQUEST", "INTERRUPTION_ALLOWED"], signal.urgency
            if (
                directed_to_amora
                and signal.active_conversation
                and signal.pause_ms >= 220
                and signal.urgency < 0.75
            ):
                return TurnState.BACKCHANNEL, ["ADDRESSEE_AMORA", "MICRO_PAUSE", "FAST_PATH"], 0.62
            return TurnState.WAIT, ["CONVERSATION_FLOOR_OTHER", "VAD_ACTIVE"], 0.9

        if signal.speech_requested:
            if other_has_floor:
                if signal.can_interrupt and signal.urgency >= 0.85:
                    return TurnState.INTERRUPT, ["URGENT_REQUEST", "CONVERSATION_FLOOR_OTHER"], signal.urgency
                return TurnState.WAIT, ["CONVERSATION_FLOOR_OTHER", "REQUEST_DEFERRED"], 0.86
            return TurnState.TAKE_TURN, ["SPEECH_REQUEST", "FLOOR_AVAILABLE"], max(0.7, signal.urgency)

        if directed_to_amora:
            if signal.vad_active:
                return TurnState.LISTEN, ["ADDRESSEE_AMORA", "SPEAKER_STILL_ACTIVE"], 0.82
            if signal.pause_ms >= 320:
                return TurnState.TAKE_TURN, ["ADDRESSEE_AMORA", "END_OF_TURN_PAUSE"], 0.8
            return TurnState.WAIT, ["ADDRESSEE_AMORA", "PAUSE_TOO_SHORT"], 0.68

        if other_has_floor:
            return TurnState.WAIT, ["ADDRESSEE_NOT_AMORA", "CONVERSATION_FLOOR_OTHER"], 0.92

        if floor == "ENTITY_AMORA":
            return TurnState.YIELD, ["AMORA_HELD_FLOOR", "NO_SPEECH_REQUEST"], 0.7

        return TurnState.LISTEN, ["NO_TURN_CLAIM"], 0.55
