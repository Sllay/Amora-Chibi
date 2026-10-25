from __future__ import annotations

import asyncio
from collections import OrderedDict
from contextlib import suppress
from time import perf_counter
from typing import Any
from uuid import UUID

from amora.events import AmoraEvent, PrivacyScope
from amora.memory import MemoryCandidate, MemoryProvenance, ProvenanceKind
from amora.models import TaskClass
from amora.world import EpistemicKind, EvidenceKind, EvidenceRef, WorldClaimDraft

from .models import CognitiveJournalEntry, CognitiveRequest, CognitiveResponsePlan, JournalStage, MessageStatus, SpeechSegment
from .proposals import CognitiveProposalBundle
from .service import CognitiveEngine as BaseCognitiveEngine, SegmentSink


_FINAL_SEGMENT_STATES = {MessageStatus.SPOKEN, MessageStatus.INTERRUPTED, MessageStatus.FAILED}
_ALLOWED_ACTION_SUGGESTIONS = {
    "initiate_contact",
    "ask_question",
    "seek_contact",
    "seek_repair",
    "express_gratitude",
    "set_boundary",
    "withdraw",
    "wait",
}


class CognitiveEngine(BaseCognitiveEngine):
    """V1 hardened cognitive engine with causal cancellation and gated reflection."""

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
            # External request cancellation/shutdown must also join the child.
            if not response_task.done():
                response_task.cancel()
                with suppress(asyncio.CancelledError):
                    await response_task
            cancel_task.cancel()
            with suppress(asyncio.CancelledError):
                await cancel_task
            self._turn_cancel_events.pop(key, None)

    async def reflect(self, request: CognitiveRequest) -> CognitiveProposalBundle:
        """Ask a provider for bounded proposals, then apply local gates.

        Memory candidates pass through MemoryWriteGate. Belief-like output is
        stored only as noncanonical HYPOTHESIS with TTL. Action suggestions are
        validated and emitted as deferred candidates; they are never executed by
        the model or promoted directly into a BDI intention.
        """
        reflection_request = request.model_copy(
            update={"metadata": {**request.metadata, "cognitive_mode": "reflection"}}
        )
        context = self.compiler.compile(reflection_request)
        descriptor = self.router.select(
            TaskClass.REASONING,
            privacy_scope=request.privacy_scope,
            remote_processing_allowed=context.remote_processing_allowed,
            require_streaming=False,
            estimated_input_tokens=max(1, len(context.model_dump_json()) // 4),
        )
        provider = self._providers.get(descriptor.name) if descriptor else None
        propose = getattr(provider, "propose", None) if provider is not None else None
        if descriptor is None or provider is None or not callable(propose):
            return CognitiveProposalBundle(reason_codes=["NO_STRUCTURED_REFLECTION_PROVIDER"])

        self._journal(
            reflection_request,
            JournalStage.REFLECTION_STARTED,
            summary="structured reflection started",
            reason_codes=["LOCAL_GATES_REMAIN_AUTHORITATIVE"],
            provider=descriptor.name,
            model=descriptor.model,
            payload={"estimated_tokens": len(context.model_dump_json()) // 4},
            dedupe_key=f"journal:reflection-started:{request.turn_id}",
        )
        started = perf_counter()
        try:
            raw = await propose(context)
            raw = dict(raw)
            usage = raw.pop("_usage", {}) if isinstance(raw.get("_usage"), dict) else {}
            bundle = CognitiveProposalBundle.model_validate(raw)
        except Exception as exc:
            self._record_usage(
                provider=descriptor.name,
                model=descriptor.model or "unknown",
                task_class=TaskClass.REASONING.value,
                success=False,
                turn_id=request.turn_id,
                latency_ms=(perf_counter() - started) * 1000.0,
                metadata={"error_code": type(exc).__name__, "phase": "reflection"},
            )
            self._journal(
                reflection_request,
                JournalStage.ERROR,
                summary="structured reflection failed",
                reason_codes=["REFLECTION_PROVIDER_FAILURE"],
                provider=descriptor.name,
                model=descriptor.model,
                error_code=type(exc).__name__,
            )
            return CognitiveProposalBundle(
                provider=descriptor.name,
                model=descriptor.model,
                reason_codes=["REFLECTION_PROVIDER_FAILURE"],
            )

        bundle.provider = descriptor.name
        bundle.model = descriptor.model
        bundle.reason_codes.extend(["STRUCTURED_PROPOSALS", "LOCAL_GATES_REMAIN_AUTHORITATIVE"])
        self._journal(
            reflection_request,
            JournalStage.PROPOSALS_GENERATED,
            summary="structured cognitive proposals generated",
            reason_codes=["PROVIDER_OUTPUT_IS_NONCANONICAL"],
            provider=descriptor.name,
            model=descriptor.model,
            payload={
                "memory_candidates": len(bundle.memory_candidates),
                "belief_hypotheses": len(bundle.belief_hypotheses),
                "action_suggestions": len(bundle.action_suggestions),
            },
        )

        for proposal in bundle.memory_candidates:
            safe_refs = {
                entity_id: confidence
                for entity_id, confidence in proposal.entity_refs.items()
                if self.compiler.identities.get(entity_id) is not None
            }
            result = self.compiler.memory.remember(
                MemoryCandidate(
                    kind=proposal.kind,
                    content=proposal.content,
                    summary=proposal.summary,
                    provenance=MemoryProvenance(
                        kind=ProvenanceKind.INFERENCE,
                        source=f"cognition:{descriptor.name}",
                        confidence=proposal.confidence,
                        source_event_id=request.source_event_id,
                        actor_id="ENTITY_AMORA",
                        session_id=request.session_id,
                    ),
                    privacy_scope=request.privacy_scope,
                    owner_entity_id=request.speaker_id if self.compiler.identities.get(request.speaker_id) else None,
                    remote_processing_allowed=False,
                    entity_refs=safe_refs,
                    tags=set(proposal.tags) | {"cognitive_reflection"},
                    importance=proposal.importance,
                    novelty=proposal.novelty,
                    emotional_weight=proposal.emotional_weight,
                    relationship_relevance=proposal.relationship_relevance,
                    future_consequence=proposal.future_consequence,
                    goal_relevance=proposal.goal_relevance,
                    metadata={"provider": descriptor.name, "model": descriptor.model, "turn_id": str(request.turn_id)},
                )
            )
            if result.memory is not None:
                bundle.accepted_memory_ids.append(str(result.memory.id))

        for proposal in bundle.belief_hypotheses:
            subject_id = proposal.subject_id
            if subject_id is not None and self.compiler.identities.get(subject_id) is None:
                subject_id = None
            evidence = []
            if request.source_event_id is not None:
                evidence.append(
                    EvidenceRef(
                        kind=EvidenceKind.EVENT,
                        ref_id=str(request.source_event_id),
                        confidence=proposal.confidence,
                        note="provider hypothesis derived from observed turn",
                    )
                )
            result = self.compiler.world.record(
                WorldClaimDraft(
                    kind=EpistemicKind.HYPOTHESIS,
                    statement=proposal.statement,
                    subject_id=subject_id,
                    predicate=proposal.predicate,
                    object_value=proposal.object_value,
                    context_key=proposal.context_key,
                    source=f"cognition:{descriptor.name}",
                    source_entity_id="ENTITY_AMORA",
                    confidence=min(0.89, proposal.confidence),
                    evidence=evidence,
                    privacy_scope=request.privacy_scope,
                    remote_processing_allowed=False,
                    metadata={"provider": descriptor.name, "model": descriptor.model, "turn_id": str(request.turn_id)},
                )
            )
            bundle.hypothesis_ids.append(str(result.claim.id))

        for suggestion in bundle.action_suggestions:
            target_id = suggestion.target_id
            target_known = target_id is None or self.compiler.identities.get(target_id) is not None
            allowed = suggestion.action_kind in _ALLOWED_ACTION_SUGGESTIONS and target_known
            await self.events.publish(
                AmoraEvent(
                    event_type="cognition.action_suggested",
                    timestamp=self.clock.now(),
                    source="cognitive_engine",
                    actor="ENTITY_AMORA",
                    target=target_id if target_known else None,
                    confidence=suggestion.confidence,
                    importance=suggestion.urgency,
                    payload={
                        "turn_id": str(request.turn_id),
                        "action_kind": suggestion.action_kind,
                        "allowed_kind": allowed,
                        "reason_code": suggestion.reason_code,
                        "decision": "deferred_to_bdi" if allowed else "rejected_by_local_gate",
                    },
                    privacy_scope=request.privacy_scope,
                    causation_id=request.source_event_id,
                    dedupe_key=f"cognition.action-suggestion:{request.turn_id}:{suggestion.action_kind}:{target_id}",
                )
            )
            bundle.deferred_action_count += 1

        elapsed = (perf_counter() - started) * 1000.0
        self._record_usage(
            provider=descriptor.name,
            model=descriptor.model or "unknown",
            task_class=TaskClass.REASONING.value,
            success=True,
            turn_id=request.turn_id,
            input_tokens=_optional_int(usage.get("input_tokens")),
            output_tokens=_optional_int(usage.get("output_tokens")),
            latency_ms=elapsed,
            status_code=_optional_int(usage.get("status_code")),
            remaining_requests=_optional_int(usage.get("remaining_requests")),
            remaining_tokens=_optional_int(usage.get("remaining_tokens")),
            metadata={"phase": "reflection"},
        )
        self._journal(
            reflection_request,
            JournalStage.PROPOSALS_GATED,
            summary="structured cognitive proposals passed through local gates",
            reason_codes=["MEMORY_WRITE_GATE", "WORLD_HYPOTHESIS_ONLY", "ACTIONS_NOT_AUTO_EXECUTED"],
            provider=descriptor.name,
            model=descriptor.model,
            duration_ms=elapsed,
            payload={
                "accepted_memory_ids": bundle.accepted_memory_ids,
                "hypothesis_ids": bundle.hypothesis_ids,
                "deferred_action_count": bundle.deferred_action_count,
            },
        )
        return bundle

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

    def cancel_speech_delivery(self, turn_id: UUID | str, *, reason: str) -> int:
        count = super().cancel_speech_delivery(turn_id, reason=reason)
        self._maybe_finish_turn(turn_id, reason_codes=["VOICE_DELIVERY_CANCELLED", reason.upper()])
        return count

    def is_turn_cancelled(self, turn_id: UUID | str) -> bool:
        return str(turn_id) in self._cancelled_turns

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


def _optional_int(value: object) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except (TypeError, ValueError):
        return None
