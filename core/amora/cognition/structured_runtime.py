from __future__ import annotations

from amora.latency_trace import trace_stage, span

import asyncio
import logging
import os
from time import perf_counter
from typing import Any

from amora.events import PrivacyScope
from amora.models import TaskClass

from .context_budget import ContextBudgetExceeded
from .adaptive_message import index_message, MessageDecompositionLimit
from .models import CognitiveContext, CognitiveRequest, CognitiveResponsePlan, JournalStage
from .self_choice import commit_self_choices
from .production import CognitiveEngine as ProductionCognitiveEngine
from .proposals import CognitiveProposalBundle
from .providers import ProviderError
from .providers_nvidia import NvidiaStreamTimeout
from .numes_stream_diagnostics import ACTIVE_NUMES_LIVENESS, NumesConnectionLiveness
from .providers_structured import GeminiCognitiveProvider
from .realization import RealizationError, RealizationValidator, ValidatedRealization, response_semantic_filter_enabled
from .speech import SpeechSegmenter
from .service import SegmentSink
from .durable_realization import (durable_realization, DeferredRealization, DURABLE_AUTHORIZATION,
    DURABLE_CAUSAL_CHECK, CausalReplanRequired, adaptive_context_allowed)
from .provider_resilience import classify_provider_failure, context_rejection
from .provider_quota import ProviderQuotaDeferred
from .surface_performance import attempt_window, ProviderPerformance, detailed_output_requested


async def _await_numes_adaptive(
    awaitable, *, timeout: float, available_seconds: float,
    grace_cap_seconds: float = 12.0, reserve_seconds: float = 5.0,
    progress_extension_seconds: float = 0.0, progress_idle_seconds: float = 23.0,
):
    """One bounded extra window only for a verifiably live Numes stream.

    The ordinary 75s attempt budget is unchanged. On expiry, an active Numes
    heartbeat can grant a small part of the existing 90s total budget. Actual
    model progress takes precedence over fallback; keepalive-only traffic
    reserves time for another provider. Neither can reset the hard ceiling.
    All cancellation joins the same task before any failover can start.
    """
    liveness = NumesConnectionLiveness()
    started = perf_counter()
    marker = ACTIVE_NUMES_LIVENESS.set(liveness)
    try:
        task = asyncio.create_task(awaitable)
    finally:
        ACTIVE_NUMES_LIVENESS.reset(marker)
    try:
        try:
            return await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        except asyncio.TimeoutError:
            if task.done():
                # A completion racing the timer must never be discarded.
                return task.result()
            if progress_extension_seconds > 0:
                # Durable jobs are independent of HTTP. Only actual recent
                # generation progress earns an extension; keepalive alone
                # cannot renew the deadline. Reserve the original fallback window.
                deadline = started + min(timeout + progress_extension_seconds,
                    available_seconds + progress_extension_seconds - reserve_seconds)
                while liveness.progressing(max_silence_seconds=progress_idle_seconds):
                    remaining = deadline - perf_counter()
                    if remaining <= 0:
                        break
                    idle_remaining = progress_idle_seconds - (perf_counter() - liveness.last_progress)
                    if idle_remaining <= 0:
                        break
                    try:
                        return await asyncio.wait_for(asyncio.shield(task),
                            timeout=min(remaining, idle_remaining))
                    except asyncio.TimeoutError:
                        if task.done():
                            return task.result()
                raise
            if not liveness.healthy():
                raise
            reserve = 0.75 if liveness.progress_deltas else reserve_seconds
            grace = min(grace_cap_seconds, max(0.0, available_seconds - timeout - reserve))
            if grace <= 0:
                raise
            logging.getLogger("amora.cognition.numes").info(
                "numes_adaptive_grace",
                extra={
                    "request_id": liveness.request_id,
                    "heartbeat_count": liveness.keepalives,
                    "progress_deltas": liveness.progress_deltas,
                    "base_timeout_seconds": timeout,
                    "grace_seconds": round(grace, 3),
                    "reason": "model_progress" if liveness.progress_deltas else "keepalive_only",
                },
            )
            return await asyncio.wait_for(asyncio.shield(task), timeout=grace)
    finally:
        if not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


class CognitiveEngine(ProductionCognitiveEngine):
    """Production cognition with Core-enforced structured surface realization.

    No provider text is queued for speech until the complete RealizationFrame has
    passed local validation. This intentionally trades a little first-token
    latency for semantic authority and safe provider failover.
    """

    def _journal(self, request, stage, **fields):
        # Only a fixed diagnostic projection leaves the privacy-controlled journal.
        # Never log context, prompts, response bodies or exception text.
        super()._journal(request, stage, **fields)
        self._log_generation_journal(request, stage, **fields)

    async def _journal_async(self, request, stage, **fields):
        if not self._async_storage_enabled(request):
            self._journal(request, stage, **fields)
            return
        await self._persist_journal_async(request, stage, **fields)
        self._log_generation_journal(request, stage, **fields)

    @staticmethod
    def _log_generation_journal(request, stage, **fields):
        if stage not in {JournalStage.CONTEXT_COMPILED, JournalStage.PROVIDER_SELECTED,
                         JournalStage.MODEL_STARTED, JournalStage.ERROR,
                         JournalStage.REFLECTION_STARTED, JournalStage.PROPOSALS_GATED}:
            return
        payload = fields.get("payload") or {}
        safe = {
            "turn_id": str(request.turn_id), "stage": stage.value,
            "provider": fields.get("provider"), "model": fields.get("model"),
            "error_code": fields.get("error_code"),
            "reason_codes": fields.get("reason_codes") or [],
            "latency_ms": fields.get("duration_ms"),
        }
        for key in ("status_code", "candidate_index", "candidate_count",
                    "provider_total_budget_seconds", "provider_attempt_budget_seconds",
                    "remaining_provider_budget_seconds", "effective_attempt_budget_seconds",
                    "reserved_failover_seconds", "memory_candidate_count", "accepted_memory_count"):
            value = payload.get(key)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                safe[key] = value
        if payload.get("phase") == "reflection":
            safe["phase"] = "reflection"
            for key in ("failure_code", "provider_error_category", "validation_issues", "retry_after_seconds"):
                if payload.get(key) is not None:
                    safe[key] = payload[key]
        logging.getLogger("amora.cognition.generation").info("generation_stage", extra=safe)

    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self._surface_performance = ProviderPerformance(self.clock)

    def state(self) -> dict[str, object]:
        state = super().state()
        state["surface_performance"] = self._surface_performance.snapshot()
        from .expressive_profile import configured_profile, profile_state
        state["social_expression_profile"] = profile_state(configured_profile())
        state["last_realization"] = getattr(self, "_last_realization", None)
        state["last_realization_rejection"] = getattr(self, "_last_realization_rejection", None)
        state["response_semantic_filter_enabled"] = response_semantic_filter_enabled()
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

    @trace_stage("speech_segment")
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
        return await super()._emit_segment(
            request, raw_text, ordinal, provider, model, on_segment, source=source,
        )

    def _segment_execution_metadata(self, request, on_segment, speech_text) -> dict[str, object]:
        # Text transport owns no voice callback. Include the already-authorized
        # flags in the initial INSERT rather than reading/re-writing that row.
        if (request.metadata.get("surface") == "platform_messaging"
                and on_segment is None and speech_text
                and request.privacy_scope not in {PrivacyScope.AUTH, PrivacyScope.SECRET}):
            return _execution_metadata(request.metadata)
        return {}

    def _after_segment_persisted(self, request, ordinal, speech_text, *, execution_persisted):
        if not speech_text or execution_persisted:
            return
        # Preserve legacy voice/callback ordering and deduplicated row recovery.
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

    @trace_stage("cognitive_response")
    async def _respond_with_failover(
        self,
        request: CognitiveRequest,
        *,
        on_segment: SegmentSink | None,
        fallback_text: str | None,
    ) -> CognitiveResponsePlan:
        from amora.messaging.generation_lifecycle import InboundGenerationLost
        started = perf_counter()
        try:
            context = await self._prepare_context_async(request)
        except ContextBudgetExceeded:
            # First AMD increment: preserve exact source and record coverage
            # without invoking a model or emitting a partial interpretation.
            try:
                coverage = index_message(request.text, max_block_bytes=8192).metadata()
            except MessageDecompositionLimit:
                coverage = {"resource_limit": True}
            await self._journal_async(request, JournalStage.ERROR,
                summary="adaptive message analysis required; consolidation unavailable",
                reason_codes=["CONTEXT_ENVELOPE_OVERFLOW", "AMD_CONSOLIDATION_REQUIRED"],
                payload={"amd_coverage": coverage})
            return await self._fallback_plan(request, "Esse contexto ficou grande demais para processar com segurança.",
                on_segment=on_segment, reason_codes=["CONTEXT_ENVELOPE_OVERFLOW", "LOCAL_FALLBACK"], started=started)
        # A verified owner-DM request can request read-only web retrieval.
        # Evidence is attached *after* canonical compilation: it is not a
        # user-forgeable memory, world claim, identity or action grant.
        web_eligible = _web_retrieval_allowed(request)
        if web_eligible:
            from amora.web_tools import WebNavigator
            navigator = getattr(self, "_owner_web_navigator", None)
            if navigator is None:
                navigator = WebNavigator(
                    workspace=getattr(self, "_owner_web_workspace_store", None),
                )
                self._owner_web_navigator = navigator
            # Only explicit search commands in a *verified owner DM* can
            # invoke the Qwen planner. Direct URLs and numbered follow-links
            # must not spend tokens on a model.
            from amora.web_tools.navigator import parse_web_intent
            web_intent = parse_web_intent(request.text)
            if web_intent is not None and web_intent.action == "search" and navigator.planner is None:
                from amora.web_tools.query_planner import GroqQueryPlanner
                groq = self._providers.get("groq")
                secrets = getattr(groq, "secrets", None)
                api_key = secrets.get("GROQ_API_KEY") if secrets is not None else None
                navigator.planner = GroqQueryPlanner(api_key, quota=self._provider_quota)
            try:
                evidence = await asyncio.wait_for(
                    navigator.research(request.text, request.session_id),
                    timeout=16.0 if (navigator.reader is not None or navigator.browser is not None
                                     or (web_intent is not None and web_intent.depth == "deep")) else 11.0,
                )
            except Exception:
                evidence = {"action": "unknown", "status": "failed",
                            "reason": "WEB_RUNTIME_FAILED", "sources": [], "references": []}
            if evidence is not None:
                context.web_research = evidence
                context.reason_codes.append(
                    "WEB_CAPTURE_PENDING" if evidence.get("status") == "capture_pending"
                    else "WEB_SOURCE_RETRIEVED" if evidence.get("sources")
                    else "WEB_RETRIEVAL_UNAVAILABLE"
                )
        estimated_tokens = max(1, len(context.model_dump_json()) // 4)
        await self._journal_async(
            request,
            JournalStage.CONTEXT_COMPILED,
            summary="canonical context compiled",
            duration_ms=(perf_counter() - started) * 1000.0,
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

        local = None if context._compiler_overflow else RealizationValidator.local_realization(context)
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
                context=context,
            )

        excluded = self._cooldown_names()
        adaptive_only = context._compiler_overflow
        candidates = self.router.ranked_candidates(
            TaskClass.SOCIAL_DIALOGUE,
            privacy_scope=request.privacy_scope,
            remote_processing_allowed=context.remote_processing_allowed,
            require_structured_output=True,
            estimated_input_tokens=estimated_tokens,
            excluded_names=excluded,
        )
        if (adaptive_only or (not candidates and not excluded)) and adaptive_context_allowed(context):
            # Discover eligible specialist interfaces without pretending that
            # the entire original fits. The specialist measures EVERY request.
            adaptive_candidates = self.router.ranked_candidates(
                TaskClass.SOCIAL_DIALOGUE, privacy_scope=request.privacy_scope,
                remote_processing_allowed=context.remote_processing_allowed,
                require_structured_output=True, estimated_input_tokens=1,
                excluded_names=excluded,
            )
            adaptive_candidates = tuple(d for d in adaptive_candidates
                if callable(getattr(self._providers.get(d.name), "realize_adaptive", None)))
            if adaptive_candidates:
                candidates = adaptive_candidates[:1]
                adaptive_only = True
        if adaptive_only and (not candidates or not adaptive_context_allowed(context)
                or not callable(getattr(self._providers.get(candidates[0].name), "realize_adaptive", None))):
            if excluded:
                raise DeferredRealization(["PROVIDERS_IN_BACKOFF"])
            raise DeferredRealization(["CONTEXT_ENVELOPE_OVERFLOW"])
        if not candidates:
            reason = "NO_ELIGIBLE_STRUCTURED_PROVIDER"
            if request.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
                reason = "REMOTE_BLOCKED_BY_PRIVACY"
            elif not request.remote_processing_allowed:
                reason = "REMOTE_PROCESSING_NOT_ALLOWED"
            elif excluded:
                reason = "PROVIDERS_IN_BACKOFF"
            if durable_realization(request) and reason == "PROVIDERS_IN_BACKOFF":
                now = self.clock.now()
                delays = [(until - now).total_seconds() for name, until in self._provider_cooldowns.items()
                          if name in excluded and until > now]
                raise DeferredRealization([reason], retry_after_seconds=min(delays, default=None))
            await self._journal_async(
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

        workload = ("visual" if context.visual_observation else "web" if context.web_research
                    else "detail" if detailed_output_requested(context) else "dialogue")
        empirical = request.metadata.get("surface") == "platform_messaging" and not adaptive_only
        if empirical:
            candidates = self._surface_performance.rank(candidates,workload,quota=self._provider_quota)
        total_budget = _positive_float(request.metadata.get("provider_total_budget_seconds"))
        attempt_budget = _positive_float(request.metadata.get("provider_attempt_budget_seconds"))
        # The caller separately bounds the entire cognitive turn. This budget
        # measures provider execution only, excluding synchronous context/SQL and
        # journal persistence; their cost must not impersonate a model timeout.
        provider_elapsed = 0.0

        is_durable = durable_realization(request)
        from .detail_sections import create_detail_session
        detail = create_detail_session(self,request,context,on_segment)
        failures = []

        async def timed_realize(realize):
            nonlocal provider_elapsed
            attempt_started = perf_counter()
            try:
                async with self._provider_quota.admission(descriptor.name, model=descriptor.model):
                    with span("provider_attempt"):
                        if detail is not None and not adaptive:
                            return await detail.run(realize,descriptor)
                        return await realize(context)
            finally:
                elapsed = max(0.0, perf_counter() - attempt_started)
                provider_elapsed += elapsed

        last_failure = "NO_STRUCTURED_REALIZATION_PROVIDER"
        attempts = [(descriptor, adaptive_only) for descriptor in candidates]
        # Do not spend extra calls on ordinary turns or substitute excerpts
        # when any compatible full-source route has not actually been tried.
        if not adaptive_only and adaptive_context_allowed(context):
            for descriptor in candidates:
                provider = self._providers.get(descriptor.name)
                if callable(getattr(provider, "realize_adaptive", None)):
                    attempts.append((descriptor, True))
                    break
        attempted_count = len(candidates)
        for provider_index, (descriptor, adaptive) in enumerate(attempts):
            if adaptive and not adaptive_only:
                if excluded or len(failures) != len(candidates) or {kind for kind, _ in failures} != {"context"}:
                    break
                attempted_count += 1
            provider = self._providers.get(descriptor.name)
            realize = getattr(provider, "realize_adaptive" if adaptive else "realize", None) if provider is not None else None
            if not callable(realize):
                last_failure = "PROVIDER_NO_REALIZATION_INTERFACE"
                self._last_realization_rejection = {
                    "provider": descriptor.name,
                    "code": last_failure,
                    "turn_id": str(request.turn_id),
                }
                await self._journal_async(
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
                remaining_budget = total_budget - provider_elapsed
                if remaining_budget <= 0:
                    last_failure = "PROVIDER_TOTAL_BUDGET_EXHAUSTED"
                    await self._journal_async(
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

            attempt_available = remaining_budget
            reserved_failover = 0.0
            if remaining_budget is not None and request.metadata.get("surface") == "platform_messaging":
                later_available = sum(
                    1 for later in candidates[provider_index+1:]
                    if callable(getattr(self._providers.get(later.name), "realize", None))
                    and self._provider_quota.remaining(later.name, model=later.model) <= 0
                )
                attempt_available, reserved_failover = attempt_window(remaining_budget,later_available)
            timeout = attempt_budget
            if attempt_available is not None:
                timeout = min(timeout,attempt_available) if timeout is not None else attempt_available
            await self._journal_async(
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
                    "remaining_provider_budget_seconds": remaining_budget,
                    "effective_attempt_budget_seconds": timeout,
                    "reserved_failover_seconds": reserved_failover,
                },
            )
            await self._journal_async(
                request,
                JournalStage.MODEL_STARTED,
                summary="structured realization generation started",
                provider=descriptor.name,
                model=descriptor.model,
            )

            if self._async_storage_enabled(request):
                session = request.session_id
                native_hash = request.metadata.get("native_message_id_hash")
                revision = request.metadata.get("transport_revision")
                if session and native_hash and isinstance(revision, int):
                    if await self.store.async_lane.call("source_invalidated", session, native_hash, revision):
                        raise InboundGenerationLost("INBOUND_SOURCE_INVALIDATED")
            begin_attempt = getattr(self, "_begin_structured_attempt", None)
            end_attempt = getattr(self, "_end_structured_attempt", None)
            if begin_attempt is not None and not begin_attempt(descriptor):
                failures.append(("temporary", None))
                last_failure = "PROVIDERS_IN_BACKOFF"
                continue
            provider_started = perf_counter()
            try:
                if timeout is not None:
                    if (descriptor.name == "numes"
                            and request.metadata.get("surface") == "platform_messaging"
                            and remaining_budget is not None):
                        raw_result = await _await_numes_adaptive(
                            timed_realize(realize),
                            timeout=max(0.001, timeout), available_seconds=attempt_available,
                            # Progress grace may use only the assigned window,
                            # never the fallback reserve or an uncharged extension.
                            reserve_seconds=0.0,
                        )
                    else:
                        raw_result = await asyncio.wait_for(timed_realize(realize), timeout=max(0.001, timeout))
                else:
                    raw_result = await timed_realize(realize)
                raw = dict(raw_result)
                usage = raw.pop("_usage", {}) if isinstance(raw.get("_usage"), dict) else {}
                with span("realization_validate"):
                    validated = RealizationValidator.parse_and_validate(
                        context, raw, enforce_dialogue_plan=response_semantic_filter_enabled(),
                    )
            except (DeferredRealization, InboundGenerationLost):
                raise
            except ProviderQuotaDeferred as exc:
                last_failure = "PROVIDER_ACCOUNT_BACKOFF"
                failures.append(("temporary", float(exc.retry_after)))
                continue
            except ContextBudgetExceeded:
                last_failure = "CONTEXT_ENVELOPE_OVERFLOW"
                failures.append(("context", None))
                await self._journal_async(request, JournalStage.ERROR, summary="provider envelope exceeds budget",
                    reason_codes=[last_failure, "AMD_CONTEXT_INSUFFICIENT" if adaptive else "NO_SURFACE_REQUEST"], error_code=last_failure)
                continue
            except NvidiaStreamTimeout as exc:
                kind = exc.diagnostics.get("timeout_kind")
                last_failure = "NVIDIA_IDLE_TIMEOUT" if kind == "idle" else "NVIDIA_TOTAL_TIMEOUT"
                failures.append(("temporary", None))
                stage = exc.diagnostics.get("stage")
                safe_stage = stage if stage in {"waiting_headers", "waiting_first_event", "reasoning_only", "visible_partial", "completed"} else "unknown"
                self._last_realization_rejection = {"provider": descriptor.name, "model": descriptor.model,
                    "code": last_failure, "turn_id": str(request.turn_id), "failure_stage": safe_stage}
                self._record_provider_failure(request, descriptor, error_code=last_failure,
                                              status_code=None, started=provider_started)
                await self._journal_async(request, JournalStage.ERROR, summary="NVIDIA stream deadline",
                    reason_codes=[last_failure, "PROVIDER_TRANSPORT_TIMEOUT", "NO_SPEECH_EMITTED"],
                    error_code=last_failure, provider=descriptor.name, model=descriptor.model,
                    payload={"failure_stage": safe_stage, "timeout_kind": "idle" if kind == "idle" else "total_safety"})
                self._backoff(descriptor, status_code=None)
                continue
            except asyncio.TimeoutError:
                if empirical and not adaptive:
                    self._surface_performance.observe(descriptor,workload,perf_counter()-provider_started,False)
                last_failure = "PROVIDER_ATTEMPT_TIMEOUT"
                failures.append(("temporary", None))
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
                await self._journal_async(
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
                failures.append(("temporary", None))
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
                await self._journal_async(
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
                failure = classify_provider_failure(exc, now=self.clock.now())
                if empirical and not adaptive and (status is None or status >= 500) and not context_rejection(exc):
                    self._surface_performance.observe(descriptor,workload,perf_counter()-provider_started,False)
                last_failure = failure.code if is_durable else type(exc).__name__
                category = ("context" if context_rejection(exc) else
                            "configuration" if status in {401, 403, 404} else "temporary")
                failures.append((category, failure.retry_after_seconds))
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
                # Envelope size is a turn-specific rejection, not global health.
                if not context_rejection(exc):
                    self._backoff(descriptor, status_code=status, retry_after=getattr(exc, "retry_after", None))
                continue
            finally:
                if end_attempt is not None:
                    end_attempt(descriptor)

            input_tokens = _optional_int(usage.get("input_tokens"))
            output_tokens = _optional_int(usage.get("output_tokens"))
            status_code = _optional_int(usage.get("status_code"))
            remaining_requests = _optional_int(usage.get("remaining_requests"))
            remaining_tokens = _optional_int(usage.get("remaining_tokens"))
            effective_latency = _optional_float(usage.get("latency_ms")) or (perf_counter() - provider_started) * 1000.0
            if detail is not None and not adaptive:
                # Each actual section was accounted against its own provider.
                # A fully recovered draft does not count as a new successful API call.
                usage_persistence = ("failed" if "failed" in detail.usage_persistence else
                    "uncertain" if "uncertain" in detail.usage_persistence else
                    "persisted" if detail.usage_persistence else "not_recorded")
                response_provider,response_model = detail.providers[-1]
            else:
                response_provider,response_model = descriptor.name,descriptor.model
                if empirical and not adaptive:
                    self._surface_performance.observe(descriptor,workload,perf_counter()-provider_started,True)
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
                    metadata={"candidate_index": provider_index, "phase": "structured_realization",
                              **({"amd_blocks": usage.get("amd_blocks"), "amd_calls": usage.get("amd_calls")} if adaptive else {})},
                )
                if (remaining_requests is not None and remaining_requests <= 0
                        or remaining_tokens is not None and remaining_tokens <= 0):
                    self._provider_quota.block_for(descriptor.name, 60, model=descriptor.model)
                self._provider_succeeded(descriptor, remaining_requests, remaining_tokens)
            reasons = [*validated.reason_codes, "REMOTE_PROVIDER_USED", "STRUCTURED_REALIZATION"]
            if detail is not None and not adaptive:
                reasons.append("DETAIL_SECTIONS_ASSEMBLED")
                if len(set(detail.providers))>1:
                    reasons.append("DETAIL_MIXED_PROVIDER_SECTIONS")
            if adaptive:
                reasons.append("AMD_GLOBAL_CONSOLIDATION_USED")
            elif provider_index:
                reasons.append("PROVIDER_FAILOVER_USED")
            return await self._plan_from_validated(
                request,
                validated,
                provider=response_provider,
                model=response_model,
                on_segment=on_segment,
                started=started,
                reason_codes=reasons,
                usage={
                    "input_tokens": input_tokens,
                    "output_tokens": output_tokens,
                    "candidate_index": provider_index,
                    "persistence": usage_persistence,
                },
                context=context,
            )

        if detail is not None and not is_durable:
            partial = detail.partial()
            if partial is not None:
                await detail.authorize()
                provider,model = detail.providers[-1]
                return await self._plan_from_validated(request,partial,
                    provider=provider,model=model,on_segment=on_segment,started=started,
                    reason_codes=list(partial.reason_codes),usage={},context=context)
        if is_durable and failures:
            kinds = {kind for kind, _ in failures}
            # A candidate skipped for cooldown or exhausted total budget may
            # recover later. Never block a turn merely because the last route
            # failed permanently while another remains temporarily unavailable.
            if not excluded and last_failure != "PROVIDER_TOTAL_BUDGET_EXHAUSTED" and len(failures) == attempted_count:
                if kinds == {"context"}:
                    raise DeferredRealization(["CONTEXT_ENVELOPE_OVERFLOW"])
                if kinds == {"configuration"}:
                    raise DeferredRealization(["PROVIDER_CONFIGURATION_BLOCKED"])
                if kinds <= {"context", "configuration"}:
                    raise DeferredRealization(["PROVIDER_CONFIGURATION_BLOCKED"])
            delays = [delay for kind, delay in failures if kind == "temporary" and delay is not None]
            raise DeferredRealization(["ALL_STRUCTURED_PROVIDERS_FAILED"],
                retry_after_seconds=max(delays, default=None))

        return await self._fallback_plan(
            request,
            fallback_text or "Hm... deu branco por um segundo.",
            on_segment=on_segment,
            reason_codes=[last_failure, "ALL_STRUCTURED_PROVIDERS_FAILED", "LOCAL_FALLBACK"],
            started=started,
        )

    def _text_finalize_enabled(self, request, on_segment):
        return (self._async_storage_enabled(request) and on_segment is None
                and not request.metadata.get("speech_device_id"))

    @trace_stage("text_batch")
    async def _persist_fresh_text(self, request, raw_segments, provider, model):
        if self.is_turn_cancelled(request.turn_id):
            return None
        records = []
        for raw in raw_segments:
            built = self._build_segment_record(request, raw, len(records), provider, model)
            if built is None:
                continue
            segment, message, fields = built
            message.metadata.update(self._segment_execution_metadata(request, None, segment.speech_text))
            records.append((segment, message, self._journal_entry(
                request, JournalStage.SPEECH_SEGMENT_READY, **fields)))
        # The remote transaction costs up to eight pipelines. Short text is
        # cheaper through the existing three-write-per-segment path.
        if len(records) < 3:
            return None
        try:
            committed = await self.store.async_lane.call(
                "persist_text_batch", records, session_id=request.session_id,
                native_hash=request.metadata.get("native_message_id_hash"),
                revision=request.metadata.get("transport_revision"),
            )
        except asyncio.CancelledError:
            # An interrupt can arrive while the worker owns the transaction.
            # The lane joins the operation before propagating cancellation.
            # Settle any newly committed rows using the existing cancellation API.
            if self.is_turn_cancelled(request.turn_id):
                self.interrupt_turn(request.turn_id, reason="text_batch_cancelled")
            raise
        if not committed:
            return None
        if self.is_turn_cancelled(request.turn_id):
            self.interrupt_turn(request.turn_id, reason="text_batch_cancelled")
            return None
        return [segment.speech_text for segment, _, _ in records]

    @trace_stage("text_finalization")
    async def _finish_validated_text(self, request, plan, validated_fields):
        # The structured path has already compiled the identical speech plan.
        # Keep the two durable journal entries (and their dedupe keys), but
        # write them together. No voice callbacks or delivery status changes.
        entries = [
            self._journal_entry(request, JournalStage.RESPONSE_PLANNED, **validated_fields),
            self._journal_entry(
                request, JournalStage.RESPONSE_PLANNED,
                summary="response plan completed",
                reason_codes=plan.reason_codes, provider=plan.provider, model=plan.model,
                duration_ms=plan.latency_ms,
                payload={"segments": len(plan.speech_segments), "fallback_used": plan.fallback_used,
                         "input_tokens": plan.input_tokens, "output_tokens": plan.output_tokens},
                dedupe_key=f"journal:response-planned:{request.turn_id}",
            ),
        ]
        await self.store.async_lane.call("put_text_final_journals", entries)

    @trace_stage("validated_plan")
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
        context: CognitiveContext | None = None,
    ) -> CognitiveResponsePlan:
        if context is not None and context.temporal_opportunity and "TEMPORAL_SILENCE_CHOSEN" in validated.reason_codes:
            await self._journal_async(request, JournalStage.TURN_FINISHED,
                summary="temporal initiative chose silence", reason_codes=reason_codes,
                provider=provider, model=model, dedupe_key=f"journal:temporal-silence:{request.turn_id}")
            return CognitiveResponsePlan(turn_id=request.turn_id, display_text="", speech_text="",
                                         provider=provider, model=model, reason_codes=reason_codes)
        # Check the complete realization before segments, journals or self
        # choices can persist a rejected group response.
        if (request.metadata.get("surface") == "platform_messaging"
                and request.metadata.get("channel_type") != "dm"):
            from amora.messaging.disclosure import public_text_allowed, audience_text_allowed
            if not (public_text_allowed(validated.text) and
                    audience_text_allowed(validated.text, audience_id=request.session_id)):
                return CognitiveResponsePlan(
                    turn_id=request.turn_id,
                    display_text="",
                    speech_text="",
                    fallback_used=True,
                    reason_codes=["CONFIDENTIAL_OUTPUT_SUPPRESSED"],
                )
        request = request.model_copy(update={
            "metadata": {**request.metadata, "response_display_text": validated.text},
        })
        speech_plan = self.normalizer.compile(
            validated.text, turn_id=request.turn_id, privacy_scope=request.privacy_scope,
        )
        segmenter = SpeechSegmenter()
        raw_segments = [*segmenter.feed(validated.text), *segmenter.flush()]
        if durable_realization(request):
            # The authorized text dispatcher commits the complete validated
            # reply with its existing outbox fence. Never create speech/history
            # rows before that transaction; a crash must not append duplicates.
            # Text-only pilot, no voice callbacks. Preserve the existing
            # first-choice-once Core gate, with live owner/lease revalidation.
            if on_segment is not None:
                raise DeferredRealization(["DURABLE_CALLBACK_UNSUPPORTED"])
            if not speech_plan.speech_text.strip():
                raise DeferredRealization(["VALIDATED_REALIZATION_NOT_SPEAKABLE"])
            guard = DURABLE_AUTHORIZATION.get()
            if guard is None or not await guard():
                raise DeferredRealization(["DURABLE_AUTHORIZATION_LOST"])
            causal_check = DURABLE_CAUSAL_CHECK.get()
            if causal_check is not None and context is not None:
                if not await causal_check(context):
                    raise CausalReplanRequired()
                # The history read may have yielded to source invalidation.
                if not await guard():
                    raise DeferredRealization(["DURABLE_AUTHORIZATION_LOST"])
            if validated.frame.self_choices and context is not None:
                try:
                    choice_decision = commit_self_choices(
                        self_model=self.compiler.self_model, request=request,
                        context=context, choices=validated.frame.self_choices,
                    )
                    reason_codes.extend(choice_decision.reason_codes)
                except Exception:
                    reason_codes.append("SELF_CHOICE_COMMIT_FAILED")
            return CognitiveResponsePlan(
                turn_id=request.turn_id, display_text=validated.text,
                speech_text=speech_plan.speech_text, speech_plan=speech_plan,
                speech_segments=[speech_plan.speech_text], provider=provider, model=model,
                reason_codes=reason_codes,
                input_tokens=_optional_int(usage.get("input_tokens")),
                output_tokens=_optional_int(usage.get("output_tokens")),
                latency_ms=(perf_counter() - started) * 1000.0,
                metadata={"realization_validated": True},
            )
        emitted: list[str] = []
        text_path = self._text_finalize_enabled(request, on_segment)
        if text_path:
            ordinal = len(await self.store.async_lane.call("list_segments", request.turn_id))
        else:
            ordinal = len(self.store.list_segments(request.turn_id))
        batched = None
        if text_path and ordinal == 0 and len(raw_segments) >= 3:
            batched = await self._persist_fresh_text(request, raw_segments, provider, model)
        if batched is not None:
            emitted = batched
        else:
            # Existing/partial history and short responses retain the canonical
            # recovery path. No failed or uncertain batch is retried here.
            if text_path and ordinal == 0 and len(raw_segments) >= 3:
                ordinal = len(await self.store.async_lane.call("list_segments", request.turn_id))
            for raw in raw_segments:
                speech = await self._emit_segment(request, raw, ordinal, provider, model, on_segment)
                if speech is not None:
                    emitted.append(speech)
                    ordinal += 1
                if text_path:
                    await asyncio.sleep(0)
        if not emitted:
            return await self._fallback_plan(
                request,
                "Hm...",
                on_segment=on_segment,
                reason_codes=["VALIDATED_REALIZATION_NOT_SPEAKABLE", "LOCAL_FALLBACK"],
                started=started,
            )

        # A self-choice is an optional first-person *proposal*, never a new
        # rule chosen by the language provider. Persist only after the entire
        # realization is valid and genuinely speakable. Test-mode never writes.
        if validated.frame.self_choices and context is not None:
            try:
                choice_decision = commit_self_choices(
                    self_model=self.compiler.self_model,
                    request=request,
                    context=context,
                    choices=validated.frame.self_choices,
                )
                reason_codes.extend(choice_decision.reason_codes)
            except Exception:
                # DB/infrastructure failure must not turn a valid response into
                # silent failure or a duplicate spoken segment.
                reason_codes.append("SELF_CHOICE_COMMIT_FAILED")
        frame_dump = validated.frame.model_dump(mode="json")
        # A personal preference proposal is not a transcript. Never record
        # the raw selection in operational diagnostics/replay journal.
        frame_dump.pop("self_choices", None)
        self._last_realization = {
            "turn_id": str(request.turn_id),
            "provider": provider,
            "model": model,
            "frame": frame_dump,
            "expression_profile": context.expression_profile if context else "core-v1",
            "reason_codes": list(reason_codes),
        }
        self._last_realization_rejection = None
        validated_fields = dict(
            summary="structured realization validated before delivery",
            reason_codes=reason_codes,
            provider=provider, model=model,
            payload={"realization_frame": frame_dump, "segments": len(emitted)},
            dedupe_key=f"journal:realization-validated:{request.turn_id}",
        )
        if not text_path:
            await self._journal_async(request, JournalStage.RESPONSE_PLANNED, **validated_fields)
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
                "web_sources": [
                    {"url": item.get("url", ""), "title": item.get("title", "")}
                    for item in (context.web_research.get("sources") or [])[:5 if context.web_research.get("depth") == "deep" else 2]
                    if item.get("status") == "opened"
                ] if context is not None and context.web_research else [],
                "web_references": [
                    str(item.get("url") or "") for item in
                    (context.web_research.get("references") or [])[:3]
                ] if context is not None and context.web_research else [],
                "web_reference_action": (
                    context.web_research.get("action")
                    if context is not None and context.web_research else None
                ),
            "dialogue_plan_semantically_enforced": "RESPONSE_SEMANTIC_FILTER_DISABLED" not in validated.reason_codes,
                "candidate_index": usage.get("candidate_index"),
                "cues": [cue.value for cue in speech_plan.cues],
                "cue_support": speech_plan.cue_support(),
                "usage_persistence": usage.get("persistence", "not_recorded"),
            },
        )
        if text_path:
            await self._finish_validated_text(request, plan, validated_fields)
        else:
            self._finish_plan(request, plan)
        return plan


def _web_retrieval_allowed(request: CognitiveRequest) -> bool:
    """Defense-in-depth gate; adapter has to verify owner before this check."""
    return bool(
        os.getenv("AMORA_WEB_ENABLED", "1").strip().lower() not in {"0", "false", "off"}
        and request.speaker_id == "PERSON_BK"
        and request.metadata.get("web_owner_verified") is True
        and request.metadata.get("surface") == "platform_messaging"
        and request.metadata.get("platform") == "discord"
        and request.metadata.get("channel_type") == "dm"
        and request.metadata.get("cognitive_mode", "reply") == "reply"
        and request.privacy_scope == PrivacyScope.PERSONAL
        and request.remote_processing_allowed
        and not request.metadata.get("test_mode")
    )


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
