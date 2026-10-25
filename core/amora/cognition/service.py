from __future__ import annotations

from collections.abc import Awaitable, Callable
from time import perf_counter
from typing import Any
from uuid import UUID

from amora.events import AmoraEvent, AsyncEventBus, PrivacyScope
from amora.models import ModelRouter, TaskClass
from amora.time import Clock

from .context import ContextCompiler
from .models import (
    CognitiveJournalEntry,
    CognitiveRequest,
    CognitiveResponsePlan,
    ConversationMessage,
    JournalStage,
    MessageSource,
    MessageStatus,
    SpeechSegment,
)
from .providers import CognitiveProvider, ProviderError
from .speech import SpeechNormalizer, SpeechSegmenter
from .store import CognitiveStore


SegmentSink = Callable[[SpeechSegment], Awaitable[None]]


class CognitiveEngine:
    """Integrates canonical Amora state with replaceable cognitive providers.

    Providers propose language. Identity, memory, beliefs, emotion, autonomy and
    delivery state remain local and persistent. The journal stores structured
    evidence/reason codes, never hidden chain-of-thought.
    """

    def __init__(
        self,
        store: CognitiveStore,
        compiler: ContextCompiler,
        router: ModelRouter,
        clock: Clock,
        events: AsyncEventBus,
        *,
        normalizer: SpeechNormalizer | None = None,
    ) -> None:
        self.store = store
        self.compiler = compiler
        self.router = router
        self.clock = clock
        self.events = events
        self.normalizer = normalizer or SpeechNormalizer()
        self._providers: dict[str, CognitiveProvider] = {}

    def close(self) -> None:
        self.store.close()

    def register_provider(self, provider: CognitiveProvider) -> None:
        self._providers[provider.descriptor.name] = provider
        self.router.register(provider.descriptor)

    def state(self) -> dict[str, object]:
        return {
            "cognitive_message_count": self.store.message_count(),
            "cognitive_journal_count": self.store.journal_count(),
            "cognitive_provider_count": self.router.provider_count,
            "cognitive_providers": [
                {
                    "name": item.name,
                    "model": item.model,
                    "available": item.available,
                    "streaming": item.supports_streaming,
                    "provider_class": item.provider_class.value,
                }
                for item in self.router.descriptors()
            ],
        }

    def observe_input(self, request: CognitiveRequest) -> None:
        self._record_input(request)

    def record_turn_decision(self, request: CognitiveRequest, state: str, reason_codes: list[str], score: float) -> None:
        self._journal(
            request,
            JournalStage.TURN_DECIDED,
            summary="turn manager decision",
            reason_codes=list(reason_codes),
            payload={"turn_state": state, "score": score},
            dedupe_key=f"journal:turn-decision:{request.turn_id}",
        )

    async def respond(
        self,
        request: CognitiveRequest,
        *,
        on_segment: SegmentSink | None = None,
        fallback_text: str | None = None,
    ) -> CognitiveResponsePlan:
        started = perf_counter()
        self._record_input(request)
        context = self.compiler.compile(request)
        self._journal(
            request,
            JournalStage.CONTEXT_COMPILED,
            summary="canonical context compiled",
            reason_codes=context.reason_codes,
            payload={
                "memory_ids": [item.id for item in context.memories],
                "world_claim_ids": [str(item.get("id")) for item in context.world_claims],
                "recent_turn_ids": [str(item.get("turn_id")) for item in context.recent_messages],
                "mood": context.mood,
                "directed_affect": context.directed_affect,
                "active_intention_kind": (context.active_intention or {}).get("kind"),
                "estimated_tokens": len(context.model_dump_json()) // 4,
                "token_budget": context.token_budget,
            },
        )

        descriptor = self.router.select(
            TaskClass.SOCIAL_DIALOGUE,
            privacy_scope=request.privacy_scope,
            remote_processing_allowed=context.remote_processing_allowed,
            require_streaming=True,
            estimated_input_tokens=max(1, len(context.model_dump_json()) // 4),
        )
        provider = self._providers.get(descriptor.name) if descriptor else None
        if descriptor is None or provider is None:
            reason = "NO_ELIGIBLE_REMOTE_PROVIDER"
            if request.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
                reason = "REMOTE_BLOCKED_BY_PRIVACY"
            elif not request.remote_processing_allowed:
                reason = "REMOTE_PROCESSING_NOT_ALLOWED"
            self._journal(request, JournalStage.PRIVACY_FILTERED, summary="remote provider skipped", reason_codes=[reason])
            return await self._fallback_plan(
                request,
                fallback_text or "Hm...",
                on_segment=on_segment,
                reason_codes=[reason, "LOCAL_FALLBACK"],
                started=started,
            )

        self._journal(
            request,
            JournalStage.PROVIDER_SELECTED,
            summary="provider selected",
            reason_codes=["ROUTER_SELECTED_PROVIDER", "STREAMING_REQUIRED"],
            provider=descriptor.name,
            model=descriptor.model,
            payload={"provider_class": descriptor.provider_class.value, "context_window_tokens": descriptor.context_window_tokens},
        )
        self._journal(
            request,
            JournalStage.MODEL_STARTED,
            summary="streaming generation started",
            provider=descriptor.name,
            model=descriptor.model,
        )

        segmenter = SpeechSegmenter()
        display_parts: list[str] = []
        emitted: list[str] = []
        ordinal = 0
        input_tokens: int | None = None
        output_tokens: int | None = None
        latency_ms: float | None = None
        status_code: int | None = None
        remaining_requests: int | None = None
        remaining_tokens: int | None = None
        try:
            async for delta in provider.stream(context):
                if delta.text:
                    display_parts.append(delta.text)
                    for raw_segment in segmenter.feed(delta.text):
                        speech = await self._emit_segment(request, raw_segment, ordinal, descriptor.name, descriptor.model, on_segment)
                        if speech is not None:
                            emitted.append(speech)
                            ordinal += 1
                input_tokens = delta.input_tokens if delta.input_tokens is not None else input_tokens
                output_tokens = delta.output_tokens if delta.output_tokens is not None else output_tokens
                latency_ms = delta.latency_ms if delta.latency_ms is not None else latency_ms
                status_code = delta.status_code if delta.status_code is not None else status_code
                remaining_requests = delta.remaining_requests if delta.remaining_requests is not None else remaining_requests
                remaining_tokens = delta.remaining_tokens if delta.remaining_tokens is not None else remaining_tokens

            for raw_segment in segmenter.flush():
                speech = await self._emit_segment(request, raw_segment, ordinal, descriptor.name, descriptor.model, on_segment)
                if speech is not None:
                    emitted.append(speech)
                    ordinal += 1

            full_display = "".join(display_parts).strip()
            if not emitted:
                return await self._fallback_plan(
                    request,
                    fallback_text or "Hm...",
                    on_segment=on_segment,
                    reason_codes=["EMPTY_PROVIDER_OUTPUT", "LOCAL_FALLBACK"],
                    started=started,
                )
            normalized = self.normalizer.normalize(full_display)
            effective_latency = latency_ms if latency_ms is not None else (perf_counter() - started) * 1000.0
            self.store.record_usage(
                provider=descriptor.name,
                model=descriptor.model or "unknown",
                task_class=TaskClass.SOCIAL_DIALOGUE.value,
                success=True,
                turn_id=request.turn_id,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=effective_latency,
                status_code=status_code,
                remaining_requests=remaining_requests,
                remaining_tokens=remaining_tokens,
            )
            plan = CognitiveResponsePlan(
                turn_id=request.turn_id,
                display_text=full_display,
                speech_text=normalized.speech_text,
                speech_segments=emitted,
                provider=descriptor.name,
                model=descriptor.model,
                reason_codes=["REMOTE_PROVIDER_USED", "STREAMING_SEGMENTATION"],
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                latency_ms=effective_latency,
                metadata={"nonverbal_cues": list(normalized.nonverbal_cues)},
            )
            self._finish_plan(request, plan)
            return plan
        except Exception as exc:
            error_code = type(exc).__name__
            status = exc.status_code if isinstance(exc, ProviderError) else None
            self.store.record_usage(
                provider=descriptor.name,
                model=descriptor.model or "unknown",
                task_class=TaskClass.SOCIAL_DIALOGUE.value,
                success=False,
                turn_id=request.turn_id,
                latency_ms=(perf_counter() - started) * 1000.0,
                status_code=status,
                metadata={"error_code": error_code},
            )
            self._journal(
                request,
                JournalStage.ERROR,
                summary="provider generation failed",
                reason_codes=["PROVIDER_FAILURE"],
                provider=descriptor.name,
                model=descriptor.model,
                error_code=error_code,
                payload={"status_code": status},
            )
            if emitted:
                plan = CognitiveResponsePlan(
                    turn_id=request.turn_id,
                    display_text="".join(display_parts).strip(),
                    speech_text=" ".join(emitted),
                    speech_segments=emitted,
                    provider=descriptor.name,
                    model=descriptor.model,
                    fallback_used=False,
                    reason_codes=["PROVIDER_FAILED_AFTER_PARTIAL_SPEECH"],
                    latency_ms=(perf_counter() - started) * 1000.0,
                )
                self._finish_plan(request, plan)
                return plan
            return await self._fallback_plan(
                request,
                fallback_text or "Hm... deu branco por um segundo.",
                on_segment=on_segment,
                reason_codes=["PROVIDER_FAILURE", "LOCAL_FALLBACK"],
                started=started,
            )

    async def _fallback_plan(
        self,
        request: CognitiveRequest,
        text: str,
        *,
        on_segment: SegmentSink | None,
        reason_codes: list[str],
        started: float,
    ) -> CognitiveResponsePlan:
        # Presentation is the complete authorized text, before vocal segmentation.
        request = request.model_copy(update={"metadata": {**request.metadata, "response_display_text": text}})
        segmenter = SpeechSegmenter()
        emitted: list[str] = []
        ordinal = len(self.store.list_segments(request.turn_id))
        for raw in [*segmenter.feed(text), *segmenter.flush()]:
            speech = await self._emit_segment(request, raw, ordinal, None, None, on_segment, source=MessageSource.LOCAL_FALLBACK)
            if speech is not None:
                emitted.append(speech)
                ordinal += 1
        normalized = self.normalizer.normalize(text)
        plan = CognitiveResponsePlan(
            turn_id=request.turn_id,
            display_text=text,
            speech_text=normalized.speech_text,
            speech_segments=emitted,
            fallback_used=True,
            reason_codes=reason_codes,
            latency_ms=(perf_counter() - started) * 1000.0,
            metadata={"nonverbal_cues": list(normalized.nonverbal_cues)},
        )
        self._finish_plan(request, plan)
        return plan

    async def _emit_segment(
        self,
        request: CognitiveRequest,
        raw_text: str,
        ordinal: int,
        provider: str | None,
        model: str | None,
        on_segment: SegmentSink | None,
        *,
        source: MessageSource = MessageSource.COGNITIVE,
    ) -> str | None:
        rendered = self.normalizer.normalize(raw_text)
        if not rendered.display_text.strip():
            return None
        if not rendered.speech_text:
            complete = request.metadata.get("response_display_text")
            if (request.metadata.get("speech_device_id") != "WEB_SAFARI"
                    or (isinstance(complete, str) and self.normalizer.normalize(complete).speech_text)):
                # Preserve the ambient/Android delivery contract. A cue-only
                # fragment within a speakable turn is carried by the full plan,
                # rather than cancelling that turn's later spoken segments.
                return None
        segment = SpeechSegment(
            turn_id=request.turn_id,
            ordinal=ordinal,
            display_text=rendered.display_text,
            speech_text=rendered.speech_text,
            queued_at=self.clock.now(),
            metadata={
                "nonverbal_cues": list(rendered.nonverbal_cues),
                "cues": [cue.value for cue in rendered.cues],
                "unsupported_cues": [cue.value for cue in rendered.cues],
                "unsupported_markers": list(rendered.unsupported_markers),
                "display_text": request.metadata.get("response_display_text", rendered.display_text),
                "privacy_scope": request.privacy_scope.value,
            },
        )
        message = ConversationMessage(
            turn_id=request.turn_id,
            speaker_id="ENTITY_AMORA",
            addressee_id=request.speaker_id,
            source=source,
            raw_text=rendered.display_text,
            normalized_text=rendered.speech_text,
            privacy_scope=request.privacy_scope,
            remote_processing_allowed=request.remote_processing_allowed,
            provider=provider,
            model=model,
            status=MessageStatus.QUEUED,
            source_event_id=request.source_event_id,
            session_id=request.session_id,
            started_at=self.clock.now(),
            metadata={"segment_id": str(segment.id), "ordinal": ordinal, "nonverbal_cues": list(rendered.nonverbal_cues)},
            dedupe_key=f"cognitive:{request.turn_id}:{ordinal}",
        )
        segment.metadata["message_id"] = str(message.id)
        self.store.put_segment(segment)
        self.store.put_message(message)
        self._journal(
            request,
            JournalStage.SPEECH_SEGMENT_READY,
            summary="speakable segment ready",
            reason_codes=["SPEECH_NORMALIZED", "SEGMENT_STABLE"],
            provider=provider,
            model=model,
            payload={"ordinal": ordinal, "segment_id": str(segment.id), "message_id": str(message.id), "chars": len(rendered.speech_text), "cues": list(rendered.nonverbal_cues)},
        )
        if on_segment is not None:
            await on_segment(segment)
        return rendered.speech_text

    def mark_segment_speaking(self, segment_id: UUID | str, *, turn_id: UUID | str | None = None) -> None:
        if self.store.set_segment_status(segment_id, MessageStatus.SPEAKING):
            segment = self._segment_by_id(segment_id, turn_id=turn_id)
            if segment:
                message_id = segment.metadata.get("message_id")
                if message_id:
                    self.store.update_message_delivery(message_id, status=MessageStatus.SPEAKING)
                self._journal_segment(segment, JournalStage.TTS_STARTED, "tts segment started")

    def acknowledge_segment(self, segment_id: UUID | str, *, spoken: bool, turn_id: UUID | str | None = None) -> None:
        status = MessageStatus.SPOKEN if spoken else MessageStatus.FAILED
        if self.store.set_segment_status(segment_id, status):
            segment = self._segment_by_id(segment_id, turn_id=turn_id)
            if segment:
                message_id = segment.metadata.get("message_id")
                if message_id:
                    self.store.update_message_delivery(message_id, status=status, spoken_text=segment.speech_text if spoken else None)
                self._journal_segment(
                    segment,
                    JournalStage.TTS_FINISHED,
                    "tts segment finished",
                    reason_codes=["SPOKEN" if spoken else "TTS_FAILED"],
                )

    def interrupt_turn(self, turn_id: UUID | str, *, reason: str = "barge_in") -> int:
        return self._cancel_delivery_segments(turn_id, reason=reason, stage=JournalStage.BARGE_IN)

    def cancel_speech_delivery(self, turn_id: UUID | str, *, reason: str) -> int:
        """Settle voice work without cancelling the response/provider task."""
        return self._cancel_delivery_segments(turn_id, reason=reason, stage=JournalStage.TTS_FINISHED)

    def _cancel_delivery_segments(self, turn_id: UUID | str, *, reason: str, stage: JournalStage) -> int:
        count = 0
        privacy_scope = PrivacyScope.PERSONAL
        for segment in self.store.list_segments(turn_id):
            try:
                privacy_scope = PrivacyScope(str(segment.metadata.get("privacy_scope") or PrivacyScope.PERSONAL.value))
            except ValueError:
                privacy_scope = PrivacyScope.PERSONAL
            if segment.status in {MessageStatus.QUEUED, MessageStatus.SPEAKING}:
                if self.store.set_segment_status(segment.id, MessageStatus.INTERRUPTED, interrupted=True):
                    count += 1
                    message_id = segment.metadata.get("message_id")
                    if message_id:
                        self.store.update_message_delivery(message_id, status=MessageStatus.INTERRUPTED, interrupted=True)
        self.store.put_journal(
            CognitiveJournalEntry(
                turn_id=UUID(str(turn_id)),
                stage=stage,
                timestamp=self.clock.now(),
                summary="speech delivery cancelled",
                reason_codes=[reason.upper()],
                payload={"segments_interrupted": count},
                privacy_scope=privacy_scope,
            )
        )
        return count

    def _segment_by_id(self, segment_id: UUID | str, *, turn_id: UUID | str | None = None) -> SpeechSegment | None:
        if turn_id is not None:
            return next((item for item in self.store.list_segments(turn_id) if str(item.id) == str(segment_id)), None)
        return None

    def _record_input(self, request: CognitiveRequest) -> None:
        dedupe = f"cognitive-input:{request.source_event_id}" if request.source_event_id else f"cognitive-input:{request.turn_id}"
        existing = self.store.get_message_by_dedupe(dedupe)
        if existing is None:
            self.store.put_message(
                ConversationMessage(
                    turn_id=request.turn_id,
                    speaker_id=request.speaker_id,
                    addressee_id=request.target_id,
                    source=MessageSource.STT if request.source_event_id else MessageSource.TEXT,
                    raw_text=request.text,
                    normalized_text=request.text.strip(),
                    privacy_scope=request.privacy_scope,
                    remote_processing_allowed=request.remote_processing_allowed,
                    stt_confidence=_as_float(request.metadata.get("stt_confidence")),
                    source_event_id=request.source_event_id,
                    session_id=request.session_id,
                    started_at=self.clock.now(),
                    status=MessageStatus.RECEIVED,
                    metadata=request.metadata,
                    dedupe_key=dedupe,
                )
            )
        self._journal(
            request,
            JournalStage.INPUT_OBSERVED,
            summary="utterance observed",
            reason_codes=["PERSISTENT_CONVERSATION_HISTORY"],
            payload={"speaker_id": request.speaker_id, "addressee_id": request.target_id, "chars": len(request.text)},
            dedupe_key=f"journal:input:{request.turn_id}",
        )

    def _finish_plan(self, request: CognitiveRequest, plan: CognitiveResponsePlan) -> None:
        plan.speech_plan = self.normalizer.compile(
            plan.display_text, turn_id=request.turn_id, privacy_scope=request.privacy_scope,
        )
        self._journal(
            request,
            JournalStage.RESPONSE_PLANNED,
            summary="response plan completed",
            reason_codes=plan.reason_codes,
            provider=plan.provider,
            model=plan.model,
            duration_ms=plan.latency_ms,
            payload={"segments": len(plan.speech_segments), "fallback_used": plan.fallback_used, "input_tokens": plan.input_tokens, "output_tokens": plan.output_tokens},
        )
        self._journal(
            request,
            JournalStage.TURN_FINISHED,
            summary="cognitive turn finished",
            reason_codes=["PLAN_READY_FOR_DELIVERY"],
            provider=plan.provider,
            model=plan.model,
        )

    def _journal(
        self,
        request: CognitiveRequest,
        stage: JournalStage,
        *,
        summary: str | None = None,
        reason_codes: list[str] | None = None,
        payload: dict[str, Any] | None = None,
        provider: str | None = None,
        model: str | None = None,
        duration_ms: float | None = None,
        error_code: str | None = None,
        dedupe_key: str | None = None,
    ) -> None:
        self.store.put_journal(
            CognitiveJournalEntry(
                turn_id=request.turn_id,
                stage=stage,
                timestamp=self.clock.now(),
                summary=summary,
                reason_codes=reason_codes or [],
                payload=payload or {},
                privacy_scope=request.privacy_scope,
                provider=provider,
                model=model,
                duration_ms=duration_ms,
                error_code=error_code,
                dedupe_key=dedupe_key,
            )
        )

    def _journal_segment(
        self,
        segment: SpeechSegment,
        stage: JournalStage,
        summary: str,
        *,
        reason_codes: list[str] | None = None,
    ) -> None:
        try:
            privacy_scope = PrivacyScope(str(segment.metadata.get("privacy_scope") or PrivacyScope.PERSONAL.value))
        except ValueError:
            privacy_scope = PrivacyScope.PERSONAL
        self.store.put_journal(
            CognitiveJournalEntry(
                turn_id=segment.turn_id,
                stage=stage,
                timestamp=self.clock.now(),
                summary=summary,
                reason_codes=reason_codes or [],
                payload={"segment_id": str(segment.id), "ordinal": segment.ordinal},
                privacy_scope=privacy_scope,
            )
        )

    async def emit_event(self, plan: CognitiveResponsePlan) -> None:
        await self.events.publish(
            AmoraEvent(
                event_type="cognition.response_planned",
                timestamp=self.clock.now(),
                source="cognitive_engine",
                actor="ENTITY_AMORA",
                confidence=1.0,
                importance=0.5,
                payload={"turn_id": str(plan.turn_id), "provider": plan.provider, "model": plan.model, "segments": len(plan.speech_segments)},
                privacy_scope=PrivacyScope.PERSONAL,
                dedupe_key=f"cognition.response:{plan.turn_id}",
            )
        )


def _as_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return max(0.0, min(1.0, result))
