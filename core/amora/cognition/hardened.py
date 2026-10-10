from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import suppress
from time import perf_counter
from typing import Any
from uuid import UUID

from amora.events import PrivacyScope

from .models import CognitiveJournalEntry, CognitiveRequest, CognitiveResponsePlan, JournalStage, MessageStatus, SpeechSegment
from .service import CognitiveEngine as BaseCognitiveEngine, SegmentSink


_FINAL_SEGMENT_STATES = {MessageStatus.SPOKEN, MessageStatus.INTERRUPTED, MessageStatus.FAILED}


class CognitiveEngine(BaseCognitiveEngine):
    """V1 hardened cognitive engine with causal cancellation.

    Barge-in is not only a playback concern. A cancelled cognitive turn must stop
    consuming the provider stream, must never enqueue later speech segments, and
    is considered finished only after every emitted segment reaches a terminal
    delivery state.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._turn_cancel_events: dict[str, asyncio.Event] = {}
        self._cancelled_turns: OrderedDict[str, None] = OrderedDict()

    async def respond(
        self,
        request: CognitiveRequest,
        *,
        on_segment: SegmentSink | None = None,
        fallback_text: str | None = None,
    ) -> CognitiveResponsePlan:
        key = str(request.turn_id)
        cancel_event = asyncio.Event()
        self._turn_cancel_events[key] = cancel_event
        if key in self._cancelled_turns:
            cancel_event.set()

        response_task = asyncio.create_task(
            super().respond(request, on_segment=on_segment, fallback_text=fallback_text),
            name=f"amora-cognition-{key}",
        )
        cancel_task = asyncio.create_task(cancel_event.wait(), name=f"amora-cognition-cancel-{key}")
        started = perf_counter()
        try:
            done, _ = await asyncio.wait({response_task, cancel_task}, return_when=asyncio.FIRST_COMPLETED)
            if response_task in done:
                return await response_task

            response_task.cancel()
            with suppress(asyncio.CancelledError):
                await response_task
            return self._interrupted_plan(request, started=started)
        finally:
            cancel_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_task
            self._turn_cancel_events.pop(key, None)

    async def _emit_segment(
        self,
        request: CognitiveRequest,
        raw_text: str,
        ordinal: int,
        provider: str | None,
        model: str | None,
        on_segment: SegmentSink | None,
        *,
        source=None,
    ) -> str | None:
        if self.is_turn_cancelled(request.turn_id):
            return None
        if source is None:
            return await super()._emit_segment(request, raw_text, ordinal, provider, model, on_segment)
        return await super()._emit_segment(
            request,
            raw_text,
            ordinal,
            provider,
            model,
            on_segment,
            source=source,
        )

    def interrupt_turn(self, turn_id: UUID | str, *, reason: str = "barge_in") -> int:
        key = str(turn_id)
        self._remember_cancelled(key)
        event = self._turn_cancel_events.get(key)
        if event is not None:
            event.set()
        count = super().interrupt_turn(turn_id, reason=reason)
        self._maybe_finish_turn(turn_id, reason_codes=[reason.upper(), "TURN_CANCELLED"])
        return count

    def acknowledge_segment(self, segment_id: UUID | str, *, spoken: bool, turn_id: UUID | str | None = None) -> None:
        super().acknowledge_segment(segment_id, spoken=spoken, turn_id=turn_id)
        if turn_id is not None:
            self._maybe_finish_turn(turn_id, reason_codes=["DELIVERY_SETTLED"])

    def is_turn_cancelled(self, turn_id: UUID | str) -> bool:
        return str(turn_id) in self._cancelled_turns

    def _finish_plan(self, request: CognitiveRequest, plan: CognitiveResponsePlan) -> None:
        # A response plan being ready is not the same thing as a conversational
        # turn being finished. TURN_FINISHED is emitted after delivery settles.
        self._journal(
            request,
            JournalStage.RESPONSE_PLANNED,
            summary="response plan completed",
            reason_codes=plan.reason_codes,
            provider=plan.provider,
            model=plan.model,
            duration_ms=plan.latency_ms,
            payload={
                "segments": len(plan.speech_segments),
                "fallback_used": plan.fallback_used,
                "input_tokens": plan.input_tokens,
                "output_tokens": plan.output_tokens,
            },
            dedupe_key=f"journal:response-planned:{request.turn_id}",
        )
        if not plan.speech_segments:
            self._put_turn_finished(
                request.turn_id,
                privacy_scope=request.privacy_scope,
                reason_codes=["NO_DELIVERY_SEGMENTS"],
            )

    def _interrupted_plan(self, request: CognitiveRequest, *, started: float) -> CognitiveResponsePlan:
        segments = self.store.list_segments(request.turn_id)
        text = " ".join(item.speech_text for item in segments).strip()
        plan = CognitiveResponsePlan(
            turn_id=request.turn_id,
            display_text=text,
            speech_text=text,
            speech_segments=[item.speech_text for item in segments],
            fallback_used=False,
            reason_codes=["TURN_INTERRUPTED", "PROVIDER_STREAM_CANCELLED"],
            latency_ms=(perf_counter() - started) * 1000.0,
            metadata={"cancelled": True},
        )
        self._finish_plan(request, plan)
        self._maybe_finish_turn(request.turn_id, reason_codes=["TURN_INTERRUPTED", "PROVIDER_STREAM_CANCELLED"])
        return plan

    def _maybe_finish_turn(self, turn_id: UUID | str, *, reason_codes: list[str]) -> None:
        segments = self.store.list_segments(turn_id)
        if not segments or any(item.status not in _FINAL_SEGMENT_STATES for item in segments):
            return
        privacy_scope = PrivacyScope.PERSONAL
        try:
            privacy_scope = PrivacyScope(str(segments[-1].metadata.get("privacy_scope") or PrivacyScope.PERSONAL.value))
        except ValueError:
            pass
        self._put_turn_finished(turn_id, privacy_scope=privacy_scope, reason_codes=reason_codes)

    def _put_turn_finished(
        self,
        turn_id: UUID | str,
        *,
        privacy_scope: PrivacyScope,
        reason_codes: list[str],
    ) -> None:
        self.store.put_journal(
            CognitiveJournalEntry(
                turn_id=UUID(str(turn_id)),
                stage=JournalStage.TURN_FINISHED,
                timestamp=self.clock.now(),
                summary="cognitive turn delivery settled",
                reason_codes=reason_codes,
                payload={
                    "segments": len(self.store.list_segments(turn_id)),
                    "cancelled": self.is_turn_cancelled(turn_id),
                },
                privacy_scope=privacy_scope,
                dedupe_key=f"journal:turn-finished:{turn_id}",
            )
        )

    def _remember_cancelled(self, key: str) -> None:
        self._cancelled_turns[key] = None
        self._cancelled_turns.move_to_end(key)
        while len(self._cancelled_turns) > 512:
            self._cancelled_turns.popitem(last=False)
