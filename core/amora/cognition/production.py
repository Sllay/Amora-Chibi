from __future__ import annotations

from time import perf_counter
from datetime import timedelta
from copy import deepcopy
from typing import Any
from uuid import UUID

from amora.events import PrivacyScope
from amora.models import ScopedModelRouter, TaskClass

from .models import CognitiveContext, CognitiveJournalEntry, CognitiveRequest, JournalStage
from .proposals import CognitiveProposalBundle
from .replay import CognitiveReplayResult
from .realization import RealizationError
from .context_budget import ContextBudgetExceeded
from .resilient import CognitiveEngine as ResilientCognitiveEngine


class CognitiveEngine(ResilientCognitiveEngine):
    """Final V1 cognition surface: resilient runtime plus safe replay/certification."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._reflection_cooldowns: dict[str, Any] = {}
        self._reflection_failures: dict[str, int] = {}
        if not isinstance(self.router, ScopedModelRouter):
            self.router = ScopedModelRouter(self.router)

    def state(self) -> dict[str, object]:
        state = super().state()
        state["cognitive_usage_ledger"] = self.store.usage_ledger()
        state["cognitive_replay_enabled"] = True
        state["cognitive_reflection_provider_failures"] = dict(self._reflection_failures)
        state["cognitive_reflection_provider_cooldowns"] = {
            name: until.isoformat() for name, until in self._reflection_cooldowns.items()
            if until > self.clock.now()
        }
        return state

    def _prepare_context(self, request: CognitiveRequest) -> CognitiveContext:
        context = super()._prepare_context(request)
        self._persist_replay_snapshot(request, context)
        return context

    async def _prepare_context_async(self, request: CognitiveRequest) -> CognitiveContext:
        from .context_v1 import ContextCompiler
        if not self._async_storage_enabled(request) or type(self.compiler) is not ContextCompiler:
            return self._prepare_context(request)
        lane = self.store.async_lane
        # Serialize snapshot construction for the migrated DM path. The
        # compiler's stateful operations remain on the owning event loop.
        async with lane.compile_lock:
            await self._record_input_async(request)
            context = await self.compiler.compile_async(request)
            entry = self._replay_snapshot_entry(request, context)
            if entry is not None:
                await lane.call("put_journal", entry)
            return context

    async def reflect(self, request: CognitiveRequest) -> CognitiveProposalBundle:
        """Run the canonical reflection gates with provider failover.

        Each attempt pins only the current asyncio context. Other turns continue
        using the normal router concurrently. Provider output is still processed
        by the single hardened reflection/gating path inherited from V1.
        """
        reflection_request = request.model_copy(
            update={"metadata": {**request.metadata, "cognitive_mode": "reflection"}}
        )
        from .context_budget import ContextBudgetExceeded
        try:
            context = self.compiler.compile(reflection_request)
        except ContextBudgetExceeded:
            return CognitiveProposalBundle(reason_codes=["CONTEXT_ENVELOPE_OVERFLOW", "REFLECTION_SKIPPED"])

        # A reflection failure must not open the social circuit or reset social quotas.
        # The transport can still have shared account-level rate limits.
        now = self.clock.now()
        excluded = {name for name, until in self._reflection_cooldowns.items() if until > now}
        candidates = self.router.ranked_candidates(
            TaskClass.REASONING,
            privacy_scope=request.privacy_scope,
            remote_processing_allowed=context.remote_processing_allowed,
            require_structured_output=True,
            estimated_input_tokens=max(1, len(context.model_dump_json()) // 4),
            excluded_names=excluded,
        )
        durable = bool(request.metadata.get("durable_memory_reflection"))
        # Specialist only handles the durable, compact extraction contract.
        # Preserve original non-memory reasoning and ranked fallback behavior.
        candidates = tuple(item for item in candidates
                           if durable or not item.metadata.get("memory_reflection_only"))
        if durable:
            candidates = tuple(sorted(candidates,
                key=lambda item: (bool(item.metadata.get("memory_primary_preference")),
                                  bool(item.metadata.get("memory_reflection_only"))), reverse=True))
        if not candidates:
            if excluded:
                bundle = CognitiveProposalBundle(reason_codes=["REFLECTION_PROVIDERS_IN_BACKOFF"])
                bundle._reflection_failure = {"retry_after_seconds": max(1.0, min(
                    (until - now).total_seconds() for name, until in self._reflection_cooldowns.items()
                    if name in excluded))}
                return bundle
            return CognitiveProposalBundle(reason_codes=["NO_STRUCTURED_REFLECTION_PROVIDER"])

        last = CognitiveProposalBundle(reason_codes=["NO_STRUCTURED_REFLECTION_PROVIDER"])
        started = perf_counter()
        for index, descriptor in enumerate(candidates):
            attempt = request
            if durable:
                remaining = 48.0 - (perf_counter() - started)
                if remaining <= 0:
                    break
                # Reserve time for failover and persistence within the queue's 60 s guard.
                attempt = request.model_copy(update={"metadata": {
                    **request.metadata, "reflection_provider_attempt_seconds": min(16.0, remaining),
                }})
            with self.router.pin(descriptor.name):
                bundle = await super().reflect(attempt)
            last = bundle
            failed = "REFLECTION_PROVIDER_FAILURE" in bundle.reason_codes
            unavailable = "NO_STRUCTURED_REFLECTION_PROVIDER" in bundle.reason_codes
            if not failed and not unavailable:
                if index:
                    bundle.reason_codes.append("REFLECTION_PROVIDER_FAILOVER_USED")
                if "REFLECTION_LEASE_LOST" not in bundle.reason_codes:
                    self._reflection_failures.pop(descriptor.name, None)
                    self._reflection_cooldowns.pop(descriptor.name, None)
                return bundle
            if failed:
                details = bundle._reflection_failure
                failures = self._reflection_failures.get(descriptor.name, 0) + 1
                self._reflection_failures[descriptor.name] = failures
                status = details.get("status_code")
                delay = details.get("retry_after_seconds")
                if delay is None:
                    delay = 60 if status == 429 else 300 if status in {401, 403, 404} else min(120, 5 * 2 ** min(5, failures - 1))
                self._reflection_cooldowns[descriptor.name] = self.clock.now() + timedelta(
                    seconds=max(1, min(3600, delay)))
        return last

    def interrupt_turn(
        self,
        turn_id: UUID | str,
        *,
        reason: str = "barge_in",
        active_segment_id: UUID | str | None = None,
        spoken_chars: int | None = None,
    ) -> int:
        count = super().interrupt_turn(turn_id, reason=reason)
        if active_segment_id is not None:
            spoken_text = self.store.record_interrupted_progress(active_segment_id, spoken_chars=spoken_chars)
            segments = self.store.list_segments(turn_id)
            privacy_scope = segments[-1].metadata.get("privacy_scope") if segments else PrivacyScope.PERSONAL.value
            try:
                scope = PrivacyScope(str(privacy_scope))
            except ValueError:
                scope = PrivacyScope.PERSONAL
            self.store.put_journal(
                CognitiveJournalEntry(
                    turn_id=UUID(str(turn_id)),
                    stage=JournalStage.TTS_FINISHED,
                    timestamp=self.clock.now(),
                    source="voice:delivery",
                    summary="tts segment interrupted",
                    reason_codes=[
                        "INTERRUPTED",
                        "SPOKEN_PREFIX_KNOWN" if spoken_chars is not None else "SPOKEN_PREFIX_UNKNOWN",
                    ],
                    payload={
                        "segment_id": str(active_segment_id),
                        "spoken_chars": spoken_chars,
                        "spoken_text": spoken_text,
                    },
                    privacy_scope=scope,
                    dedupe_key=f"journal:interrupted-progress:{active_segment_id}",
                )
            )
        return count

    async def replay(
        self,
        turn_id: UUID | str,
        *,
        provider_names: list[str] | None = None,
    ) -> list[CognitiveReplayResult]:
        """Replay an immutable local context snapshot without mutating Amora state.

        Replay never writes conversation messages, journal entries, memories,
        hypotheses, emotions, intentions or model-usage rows. It is a diagnostic
        sandbox only. AUTH/SECRET turns never receive replay snapshots.
        """
        context = self._replay_context(turn_id)
        if not context.remote_processing_allowed:
            raise ValueError("original turn did not authorize remote processing")
        if context.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            raise ValueError("sensitive turns are not replayable")

        names = provider_names or [item.name for item in self.router.descriptors()]
        results: list[CognitiveReplayResult] = []
        seen: set[str] = set()
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            provider = self._providers.get(name)
            descriptor = self.router.descriptor(name)
            if provider is None or descriptor is None:
                results.append(
                    CognitiveReplayResult(
                        provider=name,
                        success=False,
                        error_code="PROVIDER_NOT_REGISTERED",
                        reason_codes=["REPLAY_ONLY", "NO_STATE_MUTATION"],
                    )
                )
                continue
            if not descriptor.available or context.privacy_scope not in descriptor.allowed_privacy_scopes:
                results.append(
                    CognitiveReplayResult(
                        provider=name,
                        model=descriptor.model,
                        success=False,
                        error_code="PROVIDER_NOT_ELIGIBLE",
                        reason_codes=["REPLAY_ONLY", "PRIVACY_OR_AVAILABILITY_GATE"],
                    )
                )
                continue

            started = perf_counter()
            parts: list[str] = []
            finish_reason: str | None = None
            input_tokens: int | None = None
            output_tokens: int | None = None
            try:
                if callable(getattr(provider, "realize", None)):
                    # Use the undecorated transport to avoid repair counters, quota,
                    # circuits or other operational effects in read-only replay.
                    from .provider_resilience import ResilientProviderProxy
                    from .realization import RealizationValidator
                    transport = provider._provider if isinstance(provider, ResilientProviderProxy) else provider
                    raw = dict(await transport.realize(deepcopy(context)))
                    usage = raw.pop("_usage", {})
                    validated = RealizationValidator.parse_and_validate(context, raw)
                    results.append(CognitiveReplayResult(provider=name, model=descriptor.model,
                        text=validated.text, success=True, latency_ms=(perf_counter()-started)*1000,
                        input_tokens=usage.get("input_tokens"), output_tokens=usage.get("output_tokens"),
                        reason_codes=["REPLAY_ONLY", "STRUCTURED_REALIZATION", "NO_STATE_MUTATION"]))
                    continue
                async for delta in provider.stream(deepcopy(context)):
                    if delta.text:
                        parts.append(delta.text)
                    finish_reason = delta.finish_reason or finish_reason
                    input_tokens = delta.input_tokens if delta.input_tokens is not None else input_tokens
                    output_tokens = delta.output_tokens if delta.output_tokens is not None else output_tokens
                results.append(
                    CognitiveReplayResult(
                        provider=name,
                        model=descriptor.model,
                        text="".join(parts).strip(),
                        success=True,
                        latency_ms=(perf_counter() - started) * 1000.0,
                        finish_reason=finish_reason,
                        input_tokens=input_tokens,
                        output_tokens=output_tokens,
                        reason_codes=["REPLAY_ONLY", "IMMUTABLE_CONTEXT_SNAPSHOT", "NO_STATE_MUTATION"],
                    )
                )
            except Exception as exc:
                results.append(
                    CognitiveReplayResult(
                        provider=name,
                        model=descriptor.model,
                        text="".join(parts).strip(),
                        success=False,
                        latency_ms=(perf_counter() - started) * 1000.0,
                        error_code=getattr(exc, "code", type(exc).__name__),
                        reason_codes=["REPLAY_ONLY", "REALIZATION_REJECTED" if isinstance(exc, RealizationError) else "NO_SURFACE_REQUEST" if isinstance(exc, ContextBudgetExceeded) else "PROVIDER_FAILURE", "NO_STATE_MUTATION"],
                    )
                )
        return results

    def _persist_replay_snapshot(self, request: CognitiveRequest, context: CognitiveContext) -> None:
        entry = self._replay_snapshot_entry(request, context)
        if entry is not None:
            self.store.put_journal(entry)

    def _replay_snapshot_entry(self, request: CognitiveRequest, context: CognitiveContext) -> CognitiveJournalEntry | None:
        if request.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            return None
        return CognitiveJournalEntry(
            turn_id=request.turn_id,
            stage=JournalStage.CONTEXT_COMPILED,
            timestamp=self.clock.now(),
            source="cognition:replay",
            summary="immutable context snapshot for cognitive replay",
            reason_codes=["REPLAY_SNAPSHOT", "LOCAL_ONLY", "NO_CHAIN_OF_THOUGHT"],
            payload={"context": context.model_dump(mode="json")},
            privacy_scope=request.privacy_scope,
            dedupe_key=f"journal:replay-snapshot:{request.turn_id}",
        )

    def _replay_context(self, turn_id: UUID | str) -> CognitiveContext:
        for entry in self.store.journal_for_turn(turn_id):
            if "REPLAY_SNAPSHOT" not in entry.reason_codes:
                continue
            raw = entry.payload.get("context")
            if not isinstance(raw, dict):
                break
            return CognitiveContext.model_validate(raw)
        raise KeyError(f"replay snapshot not found for turn {turn_id}")
