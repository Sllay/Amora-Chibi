from __future__ import annotations

from time import perf_counter
from typing import Any
from uuid import UUID

from amora.events import PrivacyScope

from .models import CognitiveContext, CognitiveJournalEntry, CognitiveRequest, CognitiveResponsePlan, JournalStage
from .replay import CognitiveReplayResult
from .resilient import CognitiveEngine as ResilientCognitiveEngine
from .service import SegmentSink


class CognitiveEngine(ResilientCognitiveEngine):
    """Final V1 cognition surface: resilient runtime plus safe replay/certification."""

    def state(self) -> dict[str, object]:
        state = super().state()
        state["cognitive_usage_ledger"] = self.store.usage_ledger()
        state["cognitive_replay_enabled"] = True
        return state

    async def respond(
        self,
        request: CognitiveRequest,
        *,
        on_segment: SegmentSink | None = None,
        fallback_text: str | None = None,
    ) -> CognitiveResponsePlan:
        self._persist_replay_snapshot(request)
        return await super().respond(request, on_segment=on_segment, fallback_text=fallback_text)

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
                async for delta in provider.stream(context):
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
                        error_code=type(exc).__name__,
                        reason_codes=["REPLAY_ONLY", "PROVIDER_FAILURE", "NO_STATE_MUTATION"],
                    )
                )
        return results

    def _persist_replay_snapshot(self, request: CognitiveRequest) -> None:
        if request.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            return
        context = self.compiler.compile(request)
        self.store.put_journal(
            CognitiveJournalEntry(
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
