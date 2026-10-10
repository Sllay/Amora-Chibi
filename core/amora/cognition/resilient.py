from __future__ import annotations

import asyncio
from contextlib import suppress
from datetime import timedelta
from time import perf_counter
from typing import Any

from amora.events import PrivacyScope
from amora.models import ProviderDescriptor, TaskClass

from .hardened import CognitiveEngine as HardenedCognitiveEngine
from .models import CognitiveContext, CognitiveRequest, CognitiveResponsePlan, JournalStage
from .providers import ProviderError
from .providers_v1 import GeminiCognitiveProvider
from .speech import SpeechSegmenter
from .service import SegmentSink


class CognitiveEngine(HardenedCognitiveEngine):
    """V1 production cognition: cancellation + provider failover/backoff/quota."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._provider_cooldowns: dict[str, object] = {}
        self._provider_failures: dict[str, int] = {}

    def register_provider(self, provider) -> None:
        super().register_provider(provider)
        # Bootstrap keeps one explicit primary provider. The V1 engine discovers
        # the configured Gemini fallback from the same SecretStore without making
        # identity or canonical state depend on either provider.
        if provider.descriptor.name == "groq" and "gemini" not in self._providers:
            secrets = getattr(provider, "secrets", None)
            if secrets is not None:
                fallback = GeminiCognitiveProvider(secrets)
                if fallback.descriptor.available:
                    super().register_provider(fallback)

    def state(self) -> dict[str, object]:
        state = super().state()
        now = self.clock.now()
        state["cognitive_provider_cooldowns"] = {
            name: until.isoformat()
            for name, until in self._provider_cooldowns.items()
            if until > now
        }
        state["cognitive_provider_failures"] = dict(self._provider_failures)
        return state

    def _prepare_context(self, request: CognitiveRequest) -> CognitiveContext:
        """Persist the observed input and compile the one context used by this turn.

        Subclasses may observe/persist that exact object (for replay/certification),
        but must return it unchanged so provider execution never sees a separately
        recompiled snapshot.
        """
        self._record_input(request)
        return self.compiler.compile(request)

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
            self._respond_with_failover(request, on_segment=on_segment, fallback_text=fallback_text),
            name=f"amora-cognition-resilient-{key}",
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

    async def _respond_with_failover(
        self,
        request: CognitiveRequest,
        *,
        on_segment: SegmentSink | None,
        fallback_text: str | None,
    ) -> CognitiveResponsePlan:
        started = perf_counter()
        context = self._prepare_context(request)
        estimated_tokens = max(1, len(context.model_dump_json()) // 4)
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
                "estimated_tokens": estimated_tokens,
                "token_budget": context.token_budget,
            },
        )

        excluded = self._cooldown_names()
        candidates = self.router.ranked_candidates(
            TaskClass.SOCIAL_DIALOGUE,
            privacy_scope=request.privacy_scope,
            remote_processing_allowed=context.remote_processing_allowed,
            require_streaming=True,
            estimated_input_tokens=estimated_tokens,
            excluded_names=excluded,
        )
        if not candidates:
            reason = "NO_ELIGIBLE_REMOTE_PROVIDER"
            if request.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
                reason = "REMOTE_BLOCKED_BY_PRIVACY"
            elif not request.remote_processing_allowed:
                reason = "REMOTE_PROCESSING_NOT_ALLOWED"
            elif excluded:
                reason = "PROVIDERS_IN_BACKOFF"
            self._journal(request, JournalStage.PRIVACY_FILTERED, summary="remote provider skipped", reason_codes=[reason])
            return await self._fallback_plan(
                request,
                fallback_text or "Hm...",
                on_segment=on_segment,
                reason_codes=[reason, "LOCAL_FALLBACK"],
                started=started,
            )

        last_failure: str | None = None
        for provider_index, descriptor in enumerate(candidates):
            provider = self._providers.get(descriptor.name)
            if provider is None:
                continue
            self._journal(
                request,
                JournalStage.PROVIDER_SELECTED,
                summary="provider selected",
                reason_codes=[
                    "ROUTER_SELECTED_PROVIDER",
                    "STREAMING_REQUIRED",
                    "FAILOVER_PROVIDER" if provider_index else "PRIMARY_PROVIDER",
                ],
                provider=descriptor.name,
                model=descriptor.model,
                payload={
                    "provider_class": descriptor.provider_class.value,
                    "context_window_tokens": descriptor.context_window_tokens,
                    "candidate_index": provider_index,
                    "candidate_count": len(candidates),
                },
            )
            self._journal(
                request,
                JournalStage.MODEL_STARTED,
                summary="streaming generation started",
                reason_codes=["FAILOVER_ATTEMPT" if provider_index else "PRIMARY_ATTEMPT"],
                provider=descriptor.name,
                model=descriptor.model,
            )

            segmenter = SpeechSegmenter()
            display_parts: list[str] = []
            emitted: list[str] = []
            ordinal = len(self.store.list_segments(request.turn_id))
            input_tokens: int | None = None
            output_tokens: int | None = None
            latency_ms: float | None = None
            status_code: int | None = None
            remaining_requests: int | None = None
            remaining_tokens: int | None = None
            provider_started = perf_counter()
            try:
                async for delta in provider.stream(context):
                    if delta.text:
                        display_parts.append(delta.text)
                        for raw_segment in segmenter.feed(delta.text):
                            speech = await self._emit_segment(
                                request,
                                raw_segment,
                                ordinal,
                                descriptor.name,
                                descriptor.model,
                                on_segment,
                            )
                            if speech:
                                emitted.append(speech)
                                ordinal += 1
                    input_tokens = delta.input_tokens if delta.input_tokens is not None else input_tokens
                    output_tokens = delta.output_tokens if delta.output_tokens is not None else output_tokens
                    latency_ms = delta.latency_ms if delta.latency_ms is not None else latency_ms
                    status_code = delta.status_code if delta.status_code is not None else status_code
                    remaining_requests = delta.remaining_requests if delta.remaining_requests is not None else remaining_requests
                    remaining_tokens = delta.remaining_tokens if delta.remaining_tokens is not None else remaining_tokens

                for raw_segment in segmenter.flush():
                    speech = await self._emit_segment(
                        request,
                        raw_segment,
                        ordinal,
                        descriptor.name,
                        descriptor.model,
                        on_segment,
                    )
                    if speech:
                        emitted.append(speech)
                        ordinal += 1

                if not emitted:
                    last_failure = "EMPTY_PROVIDER_OUTPUT"
                    self._record_provider_failure(
                        request,
                        descriptor,
                        error_code=last_failure,
                        status_code=status_code,
                        started=provider_started,
                    )
                    self._backoff(descriptor, status_code=status_code)
                    continue

                effective_latency = latency_ms if latency_ms is not None else (perf_counter() - provider_started) * 1000.0
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
                    metadata={"candidate_index": provider_index},
                )
                self._provider_succeeded(descriptor, remaining_requests, remaining_tokens)
                full_display = "".join(display_parts).strip()
                normalized = self.normalizer.normalize(full_display)
                reasons = ["REMOTE_PROVIDER_USED", "STREAMING_SEGMENTATION"]
                if provider_index:
                    reasons.append("PROVIDER_FAILOVER_USED")
                plan = CognitiveResponsePlan(
                    turn_id=request.turn_id,
                    display_text=full_display,
                    speech_text=normalized.speech_text,
                    speech_segments=emitted,
                    provider=descriptor.name,
                    model=descriptor.model,
                    reason_codes=reasons,
                    input_tokens=input_tokens,
                    output_tokens=output_tokens,
                    latency_ms=(perf_counter() - started) * 1000.0,
                    metadata={"nonverbal_cues": list(normalized.nonverbal_cues), "candidate_index": provider_index},
                )
                self._finish_plan(request, plan)
                return plan
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                status = exc.status_code if isinstance(exc, ProviderError) else None
                last_failure = type(exc).__name__
                self._record_provider_failure(
                    request,
                    descriptor,
                    error_code=last_failure,
                    status_code=status,
                    started=provider_started,
                )
                self._backoff(descriptor, status_code=status, retry_after=getattr(exc, "retry_after", None))
                if emitted:
                    plan = CognitiveResponsePlan(
                        turn_id=request.turn_id,
                        display_text="".join(display_parts).strip(),
                        speech_text=" ".join(emitted),
                        speech_segments=emitted,
                        provider=descriptor.name,
                        model=descriptor.model,
                        fallback_used=False,
                        reason_codes=["PROVIDER_FAILED_AFTER_PARTIAL_SPEECH", "FAILOVER_BLOCKED_AFTER_SPEECH"],
                        latency_ms=(perf_counter() - started) * 1000.0,
                    )
                    self._finish_plan(request, plan)
                    return plan

        return await self._fallback_plan(
            request,
            fallback_text or "Hm... deu branco por um segundo.",
            on_segment=on_segment,
            reason_codes=[last_failure or "ALL_PROVIDERS_FAILED", "ALL_REMOTE_PROVIDERS_FAILED", "LOCAL_FALLBACK"],
            started=started,
        )

    def _record_provider_failure(
        self,
        request: CognitiveRequest,
        descriptor: ProviderDescriptor,
        *,
        error_code: str,
        status_code: int | None,
        started: float,
    ) -> None:
        self.store.record_usage(
            provider=descriptor.name,
            model=descriptor.model or "unknown",
            task_class=TaskClass.SOCIAL_DIALOGUE.value,
            success=False,
            turn_id=request.turn_id,
            latency_ms=(perf_counter() - started) * 1000.0,
            status_code=status_code,
            metadata={"error_code": error_code, "failover_eligible": True},
        )
        self._journal(
            request,
            JournalStage.ERROR,
            summary="provider generation failed",
            reason_codes=["PROVIDER_FAILURE", "FAILOVER_ELIGIBLE"],
            provider=descriptor.name,
            model=descriptor.model,
            error_code=error_code,
            payload={"status_code": status_code},
        )

    def _backoff(
        self,
        descriptor: ProviderDescriptor,
        *,
        status_code: int | None,
        retry_after: str | None = None,
    ) -> None:
        failures = self._provider_failures.get(descriptor.name, 0) + 1
        self._provider_failures[descriptor.name] = failures
        seconds = _retry_after_seconds(retry_after)
        if seconds is None:
            if status_code == 429:
                seconds = 60.0
            elif status_code is not None and 400 <= status_code < 500:
                seconds = 300.0
            else:
                seconds = min(120.0, 5.0 * (2 ** min(5, failures - 1)))
        until = self.clock.now() + timedelta(seconds=max(1.0, min(300.0, seconds)))
        self._provider_cooldowns[descriptor.name] = until
        if status_code == 429:
            self.router.set_quota(descriptor.name, 0.0)

    def _provider_succeeded(
        self,
        descriptor: ProviderDescriptor,
        remaining_requests: int | None,
        remaining_tokens: int | None,
    ) -> None:
        self._provider_failures.pop(descriptor.name, None)
        self._provider_cooldowns.pop(descriptor.name, None)
        if remaining_requests is not None and remaining_requests <= 0:
            self.router.set_quota(descriptor.name, 0.0)
            self._provider_cooldowns[descriptor.name] = self.clock.now() + timedelta(seconds=60)
        elif remaining_tokens is not None and remaining_tokens <= 0:
            self.router.set_quota(descriptor.name, 0.0)
            self._provider_cooldowns[descriptor.name] = self.clock.now() + timedelta(seconds=60)
        else:
            self.router.set_quota(descriptor.name, None)

    def _cooldown_names(self) -> set[str]:
        now = self.clock.now()
        expired = [name for name, until in self._provider_cooldowns.items() if until <= now]
        for name in expired:
            self._provider_cooldowns.pop(name, None)
            if self.router.descriptor(name) is not None:
                self.router.set_quota(name, None)
        return {name for name, until in self._provider_cooldowns.items() if until > now}


def _retry_after_seconds(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value.strip())
    except (TypeError, ValueError):
        return None
