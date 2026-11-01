from __future__ import annotations

import asyncio
from time import perf_counter
from typing import Any

from amora.events import PrivacyScope
from amora.models import TaskClass

from .models import CognitiveRequest, CognitiveResponsePlan, JournalStage
from .production import CognitiveEngine as ProductionCognitiveEngine
from .proposals import CognitiveProposalBundle
from .providers import ProviderError
from .providers_structured import GeminiCognitiveProvider
from .realization import RealizationError, RealizationValidator, ValidatedRealization
from .speech import SpeechSegmenter
from .service import SegmentSink


class CognitiveEngine(ProductionCognitiveEngine):
    """Production cognition with Core-enforced structured surface realization.

    No provider text is queued for speech until the complete RealizationFrame has
    passed local validation. This intentionally trades a little first-token
    latency for semantic authority and safe provider failover.
    """

    def state(self) -> dict[str, object]:
        state = super().state()
        state["last_realization"] = getattr(self, "_last_realization", None)
        state["last_realization_rejection"] = getattr(self, "_last_realization_rejection", None)
        return state

    def register_provider(self, provider) -> None:
        super().register_provider(provider)
        if provider.descriptor.name == "groq":
            secrets = getattr(provider, "secrets", None)
            if secrets is not None:
                structured = GeminiCognitiveProvider(secrets)
                if structured.descriptor.available:
                    self._providers["gemini"] = structured
                    self.router.register(structured.descriptor)

    async def reflect(self, request: CognitiveRequest) -> CognitiveProposalBundle:
        if bool(request.metadata.get("test_mode", False)) or request.metadata.get("memory_consolidation") is False:
            self._journal(
                request,
                JournalStage.PROPOSALS_GATED,
                summary="persistent reflection disabled for technical test turn",
                reason_codes=["TEST_MODE", "PERSISTENT_REFLECTION_DISABLED"],
                payload={
                    "memory_consolidation": request.metadata.get("memory_consolidation"),
                    "relationship_learning": request.metadata.get("relationship_learning"),
                },
                dedupe_key=f"journal:test-reflection-gate:{request.turn_id}",
            )
            return CognitiveProposalBundle(
                reason_codes=["TEST_MODE", "PERSISTENT_REFLECTION_DISABLED", "NO_STATE_MUTATION"]
            )
        return await super().reflect(request)

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
        speech = await super()._emit_segment(
            request,
            raw_text,
            ordinal,
            provider,
            model,
            on_segment,
            source=source,
        )
        if not speech:
            return speech
        execution = _execution_metadata(request.metadata)
        if execution:
            segment = next(
                (item for item in self.store.list_segments(request.turn_id) if item.ordinal == ordinal),
                None,
            )
            if segment is not None:
                message_id = segment.metadata.get("message_id")
                if message_id:
                    self.store.merge_message_metadata(str(message_id), execution)
        return speech

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
                "dialogue_plan": context.dialogue_plan,
                "active_intention_kind": (context.active_intention or {}).get("kind"),
                "estimated_tokens": estimated_tokens,
                "token_budget": context.token_budget,
                "test_mode": bool(request.metadata.get("test_mode", False)),
            },
        )

        local = RealizationValidator.local_realization(context)
        if local is not None:
            return await self._plan_from_validated(
                request,
                local,
                provider=None,
                model=None,
                on_segment=on_segment,
                started=started,
                reason_codes=[*local.reason_codes],
                usage={},
            )

        excluded = self._cooldown_names()
        candidates = self.router.ranked_candidates(
            TaskClass.SOCIAL_DIALOGUE,
            privacy_scope=request.privacy_scope,
            remote_processing_allowed=context.remote_processing_allowed,
            require_structured_output=True,
            estimated_input_tokens=estimated_tokens,
            excluded_names=excluded,
        )
        if not candidates:
            reason = "NO_ELIGIBLE_STRUCTURED_PROVIDER"
            if request.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
                reason = "REMOTE_BLOCKED_BY_PRIVACY"
            elif not request.remote_processing_allowed:
                reason = "REMOTE_PROCESSING_NOT_ALLOWED"
            elif excluded:
                reason = "PROVIDERS_IN_BACKOFF"
            self._journal(
                request,
                JournalStage.PRIVACY_FILTERED,
                summary="structured remote realization skipped",
                reason_codes=[reason],
            )
            return await self._fallback_plan(
                request,
                fallback_text or "Hm...",
                on_segment=on_segment,
                reason_codes=[reason, "LOCAL_FALLBACK"],
                started=started,
            )

        total_budget = _positive_float(request.metadata.get("provider_total_budget_seconds"))
        attempt_budget = _positive_float(request.metadata.get("provider_attempt_budget_seconds"))
        last_failure = "NO_STRUCTURED_REALIZATION_PROVIDER"
        for provider_index, descriptor in enumerate(candidates):
            provider = self._providers.get(descriptor.name)
            realize = getattr(provider, "realize", None) if provider is not None else None
            if not callable(realize):
                last_failure = "PROVIDER_NO_REALIZATION_INTERFACE"
                self._last_realization_rejection = {
                    "provider": descriptor.name,
                    "code": last_failure,
                    "turn_id": str(request.turn_id),
                }
                self._journal(
                    request,
                    JournalStage.ERROR,
                    summary="provider skipped: no structured realization interface",
                    reason_codes=[last_failure, "NO_SPEECH_EMITTED"],
                    provider=descriptor.name,
                    model=descriptor.model,
                )
                continue

            remaining_budget = None
            if total_budget is not None:
                remaining_budget = total_budget - (perf_counter() - started)
                if remaining_budget <= 0:
                    last_failure = "PROVIDER_TOTAL_BUDGET_EXHAUSTED"
                    self._journal(
                        request,
                        JournalStage.ERROR,
                        summary="provider failover budget exhausted before next attempt",
                        reason_codes=[last_failure, "NO_SPEECH_EMITTED"],
                        provider=descriptor.name,
                        model=descriptor.model,
                        error_code=last_failure,
                        payload={"candidate_index": provider_index},
                    )
                    break

            self._journal(
                request,
                JournalStage.PROVIDER_SELECTED,
                summary="structured realization provider selected",
                reason_codes=[
                    "ROUTER_SELECTED_PROVIDER",
                    "STRUCTURED_REALIZATION_REQUIRED",
                    "FAILOVER_PROVIDER" if provider_index else "PRIMARY_PROVIDER",
                ],
                provider=descriptor.name,
                model=descriptor.model,
                payload={
                    "candidate_index": provider_index,
                    "candidate_count": len(candidates),
                    "provider_total_budget_seconds": total_budget,
                    "provider_attempt_budget_seconds": attempt_budget,
                },
            )
            self._journal(
                request,
                JournalStage.MODEL_STARTED,
                summary="structured realization generation started",
                provider=descriptor.name,
                model=descriptor.model,
            )

            provider_started = perf_counter()
            try:
                timeout = attempt_budget
                if remaining_budget is not None:
                    timeout = min(timeout, remaining_budget) if timeout is not None else remaining_budget
                if timeout is not None:
                    raw_result = await asyncio.wait_for(realize(context), timeout=max(0.05, timeout))
                else:
                    raw_result = await realize(context)
                raw = dict(raw_result)
                usage = raw.pop("_usage", {}) if isinstance(raw.get("_usage"), dict) else {}
                validated = RealizationValidator.parse_and_validate(context, raw)
            except asyncio.TimeoutError:
                last_failure = "PROVIDER_ATTEMPT_TIMEOUT"
                self._last_realization_rejection = {
                    "provider": descriptor.name,
                    "model": descriptor.model,
                    "code": last_failure,
                    "turn_id": str(request.turn_id),
                }
                # This timeout belongs to the caller's turn budget, not to the
                # provider's global health. Record it for usage/observability and
                # allow failover, but do not poison provider cooldowns used by Web,
                # voice or unrelated surfaces.
                self._record_provider_failure(
                    request,
                    descriptor,
                    error_code=last_failure,
                    status_code=None,
                    started=provider_started,
                )
                self._journal(
                    request,
                    JournalStage.ERROR,
                    summary="provider attempt exceeded turn budget",
                    reason_codes=[last_failure, "SURFACE_BUDGET_TIMEOUT", "FAILOVER_ELIGIBLE", "NO_SPEECH_EMITTED"],
                    provider=descriptor.name,
                    model=descriptor.model,
                    error_code=last_failure,
                    payload={"candidate_index": provider_index, "timeout_seconds": timeout},
                )
                continue
            except RealizationError as exc:
                last_failure = exc.code
                self._last_realization_rejection = {
                    "provider": descriptor.name,
                    "model": descriptor.model,
                    "code": exc.code,
                    "turn_id": str(request.turn_id),
                }
                self._record_usage(
                    provider=descriptor.name,
                    model=descriptor.model or "unknown",
                    task_class=TaskClass.SOCIAL_DIALOGUE.value,
                    success=False,
                    turn_id=request.turn_id,
                    latency_ms=(perf_counter() - provider_started) * 1000.0,
                    metadata={"error_code": exc.code, "phase": "realization_validation"},
                )
                self._journal(
                    request,
                    JournalStage.ERROR,
                    summary="provider realization rejected before speech",
                    reason_codes=["REALIZATION_REJECTED", exc.code, "NO_SPEECH_EMITTED"],
                    provider=descriptor.name,
                    model=descriptor.model,
                    error_code=exc.code,
                )
                continue
            except Exception as exc:
                status = exc.status_code if isinstance(exc, ProviderError) else None
                last_failure = type(exc).__name__
                self._last_realization_rejection = {
                    "provider": descriptor.name,
                    "model": descriptor.model,
                    "code": last_failure,
                    "turn_id": str(request.turn_id),
                }
                self._record_provider_failure(
                    request,
                    descriptor,
                    error_code=last_failure,
                    status_code=status,
                    started=provider_started,
                )
                self._backoff(descriptor, status_code=status, retry_after=getattr(exc, "retry_after", None))
                continue

            input_tokens = _optional_int(usage.get("input_tokens"))
            output_tokens = _optional_int(usage.get("output_tokens"))
            status_code = _optional_int(usage.get("status_code"))
            remaining_requests = _optional_int(usage.get("remaining_requests"))
            remaining_tokens = _optional_int(usage.get("remaining_tokens"))
            effective_latency = _optional_float(usage.get("latency_ms")) or (perf_counter() - provider_started) * 1000.0
            usage_persistence = self._record_usage(
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
                metadata={"candidate_index": provider_index, "phase": "structured_realization"},
            )
            self._provider_succeeded(descriptor, remaining_requests, remaining_tokens)
            reasons = [*validated.reason_codes, "REMOTE_PROVIDER_USED", "STRUCTURED_REALIZATION"]
            if provider_index:
                reasons.append("PROVIDER_FAILOVER_USED")
            return await self._plan_from_validated(
                request,
                validated,
                provider=descriptor.name,
                model=descriptor.model,
                on_segment=on_segment,
                started=started,
                reason_codes=reasons,
                usage={
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "candidate_index": provider_index,
                    "persistence": usage_persistence,
                },
            )

        return await self._fallback_plan(
            request,
            fallback_text or "Hm... deu branco por um segundo.",
            on_segment=on_segment,
            reason_codes=[last_failure, "ALL_STRUCTURED_PROVIDERS_FAILED", "LOCAL_FALLBACK"],
            started=started,
        )

    async def _plan_from_validated(
        self,
        request: CognitiveRequest,
        validated: ValidatedRealization,
        *,
        provider: str | None,
        model: str | None,
        on_segment: SegmentSink | None,
        started: float,
        reason_codes: list[str],
        usage: dict[str, Any],
    ) -> CognitiveResponsePlan:
        request = request.model_copy(update={
            "metadata": {**request.metadata, "response_display_text": validated.text},
        })
        speech_plan = self.normalizer.compile(
            validated.text, turn_id=request.turn_id, privacy_scope=request.privacy_scope,
        )
        segmenter = SpeechSegmenter()
        emitted: list[str] = []
        ordinal = len(self.store.list_segments(request.turn_id))
        for raw in [*segmenter.feed(validated.text), *segmenter.flush()]:
            speech = await self._emit_segment(
                request,
                raw,
                ordinal,
                provider,
                model,
                on_segment,
            )
            if speech is not None:
                emitted.append(speech)
                ordinal += 1
        if not emitted:
            return await self._fallback_plan(
                request,
                "Hm...",
                on_segment=on_segment,
                reason_codes=["VALIDATED_REALIZATION_NOT_SPEAKABLE", "LOCAL_FALLBACK"],
                started=started,
            )

        frame_dump = validated.frame.model_dump(mode="json")
        self._last_realization = {
            "turn_id": str(request.turn_id),
            "provider": provider,
            "model": model,
            "frame": frame_dump,
            "reason_codes": list(reason_codes),
        }
        self._last_realization_rejection = None
        self._journal(
            request,
            JournalStage.RESPONSE_PLANNED,
            summary="structured realization validated before delivery",
            reason_codes=reason_codes,
            provider=provider,
            model=model,
            payload={
                "realization_frame": frame_dump,
                "segments": len(emitted),
            },
            dedupe_key=f"journal:realization-validated:{request.turn_id}",
        )
        plan = CognitiveResponsePlan(
            turn_id=request.turn_id,
            display_text=validated.text,
            speech_text=speech_plan.speech_text,
            speech_plan=speech_plan,
            speech_segments=emitted,
            provider=provider,
            model=model,
            reason_codes=reason_codes,
            input_tokens=_optional_int(usage.get("input_tokens")),
            output_tokens=_optional_int(usage.get("output_tokens")),
            latency_ms=(perf_counter() - started) * 1000.0,
            metadata={
                "realization_frame": frame_dump,
                "realization_validated": True,
                "candidate_index": usage.get("candidate_index"),
                "cues": [cue.value for cue in speech_plan.cues],
                "cue_support": speech_plan.cue_support(),
                "usage_persistence": usage.get("persistence", "not_recorded"),
            },
        )
        self._finish_plan(request, plan)
        return plan


def _execution_metadata(metadata: dict[str, Any]) -> dict[str, object]:
    keys = ("test_mode", "memory_consolidation", "relationship_learning", "emotion_persistence")
    return {key: metadata[key] for key in keys if key in metadata}


def _positive_float(value: object) -> float | None:
    try:
        parsed = float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
    if parsed is None or parsed <= 0:
        return None
    return parsed


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
