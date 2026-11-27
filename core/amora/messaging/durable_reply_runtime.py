"""Opt-in RUN 01 text-DM dispatcher using the existing Core stores.

No automatic schema installation or environment activation. The deployment
gate must prepare the additive schema and reviewed registry first. Ordinary
legacy requests keep the original synchronous path until explicitly enabled.
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import random
from contextlib import suppress
from time import monotonic
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from amora.cognition import CognitiveRequest
from amora.cognition.durable_realization import (DURABLE_REALIZATION, DeferredRealization,
    DURABLE_AUTHORIZATION, DURABLE_CAUSAL_CHECK, CausalReplanRequired)
from amora.cognition.models import JournalStage
from amora.cognition.speech import SpeechSegmenter
from amora.events import PrivacyScope
from amora.web_tools import parse_web_intent
from .explicit_memory import is_verified_owner
from .generation_lifecycle import InboundGenerationBusy, InboundGenerationLost
from .models import AttentionDecision, ChannelType, InboundMessage, InboundResult, RoutingAction
from .reply_jobs import ReplyJobRepository, ReplyReason
from .reply_diagnostics import ReplyDiagnostics
from .visual_reference import visual_reference_intent

_LOG = logging.getLogger("amora.messaging.durable_reply")


class DurableReplyRuntime:
    """One lightweight dispatcher; no provider or database transaction overlap."""

    def __init__(self, service):
        self.service = service
        self.repo = ReplyJobRepository(service.store.database, service.clock)
        self.active = False
        self.accepting = True
        self.task = None
        self.wake = None
        self._next_reconcile = 0.0
        self._execution = asyncio.Lock()
        self.diagnostics = ReplyDiagnostics()

    async def call(self, operation, *args, **kwargs):
        lane = self.service.storage_lane
        if lane is not None:
            return await lane.call("reply_job", operation, *args, **kwargs)
        if self.service.store.database.backend.capabilities.remote:
            raise RuntimeError("DURABLE_REPLY_STORAGE_LANE_REQUIRED")
        if operation == "finalize":
            claim, *rest = args
            return self.repo.finalize(claim, self.service.store, *rest)
        if operation in {"stage", "prepared"}:
            return getattr(self.repo.generations, operation)(*args)
        return getattr(self.repo, operation)(*args, **kwargs)

    def configure_realtime(self, receipt_port, *, recover_orphans=False):
        """Explicit integration/lab injection; no automatic schema or environment activation."""
        if not self.active or self.service.realtime_bridge is not None:
            raise RuntimeError("REALTIME_CONFIGURATION_NOT_ALLOWED")
        from .realtime_bridge import RealtimeBridge
        self.service.realtime_bridge=RealtimeBridge(self.service,receipt_port,recover_orphans=recover_orphans)
        return self.service.realtime_bridge

    async def _operational_receipt(self,operation,*args,**kwargs):
        lane=self.service.storage_lane
        if lane is not None:
            return await lane.call("realtime_receipt",operation,*args,**kwargs)
        db=self.service.store.database
        if db.backend.capabilities.remote:
            raise RuntimeError("REALTIME_STORAGE_LANE_REQUIRED")
        from amora.latency_trace import trace_lock
        from amora.storage.realtime_receipt_migration import verify_sqlite_receipts
        from .realtime_receipts import RealtimeReceiptStore
        with trace_lock(db._lock):
            if operation=="verify":
                return verify_sqlite_receipts(db._conn)
            if operation not in {"read","prepare","confirm","reject_definitive","observe_create","orphan_candidates","orphan_read"}:
                raise ValueError("REALTIME_RECEIPT_OPERATION_NOT_ALLOWED")
            return getattr(RealtimeReceiptStore(db._conn,reviewed=True),operation)(*args,**kwargs)

    async def start(self, *, background=True):
        if self.active:
            return
        self.service.start_storage_lane()
        # Missing candidate table/registry fails closed before accepting a turn.
        await self.call("verify_schema")
        await self.call("counts")
        from amora.storage.reply_job_rollout import realtime_enabled
        enable_realtime=realtime_enabled()
        if enable_realtime:
            await self._operational_receipt("verify")
        self.active = True
        if enable_realtime:
            self.configure_realtime(self._operational_receipt,recover_orphans=True)
        self.wake = asyncio.Event()
        self._next_reconcile = 0.0
        if background:
            self.task = asyncio.create_task(self._loop(), name="amora-durable-replies")

    async def stop(self):
        self.active = False
        if self.task is not None:
            self.task.cancel()
            await asyncio.gather(self.task, return_exceptions=True)
            self.task = None
        self.wake = None
        self.service.realtime_bridge = None

    def notify(self):
        if self.wake is not None:
            self.wake.set()

    def eligible(self, message, decision):
        # Pilot scope: pure text, verified owner DM, explicit remote consent.
        # Web operations and native media/expressions remain legacy until their
        # independently recoverable contracts are implemented. No raw bytes,
        # signed URLs or native expression catalog in the recovery descriptor.
        return bool(self.active and decision.action == RoutingAction.RESPOND
            and message.platform.value == "discord"
            and message.channel_type == ChannelType.DM
            and message.privacy_scope == PrivacyScope.PERSONAL
            and message.remote_processing_allowed and message.text.strip()
            and not message.attachments and not message.incoming_image and not message.incoming_images
            and not message.expressions and not message.incoming_image_status
            and len(message.sender_identifiers) == 1
            and message.sender_identifiers[0].identifier_type == "discord_user_id"
            and parse_web_intent(message.text) is None
            and visual_reference_intent(message) is None
            and is_verified_owner(self.service.attention.profiles, message,
                                  decision.speaker_entity_id))

    def transport_metadata(self, message, decision):
        if not self.eligible(message, decision):
            return {}
        # The audit row already owns text and transport IDs. Only the missing
        # reconstruction fields are added, not a second inbound payload.
        return {"durable_reply_transport": {
            "native_message_id": message.native_message_id,
            "transport_revision": message.transport_revision,
            "sender_identifiers": [item.model_dump(mode="json")
                                   for item in message.sender_identifiers],
            "reply_to_author_identifiers": [item.model_dump(mode="json")
                                           for item in message.reply_to_author_identifiers],
            "mentions_amora": message.mentions_amora,
            "reply_to_amora": message.reply_to_amora,
        }}

    def stop_admissions(self):
        # Do not stop recovery or invalidate already accepted turns.
        self.accepting = False

    async def admission_guard(self, message):
        if not self.active or self.accepting:
            return
        speaker, actor_ref = self.service.attention.resolve_speaker(message)
        decision = AttentionDecision(action=RoutingAction.RESPOND,
            speaker_entity_id=speaker, speaker_actor_ref=actor_ref, score=1.0)
        if self.eligible(message, decision):
            # Maintenance requires paused ingress. Never bypass the queue and
            # generate out of order while older jobs are still being drained.
            raise InboundGenerationBusy("DURABLE_REPLY_DRAINING")

    async def status(self):
        counts = await self.call("counts")
        return {"mode": "accept" if self.accepting else "drain",
                "active": self.active, "counts": counts,
                "diagnostics": self.diagnostics.snapshot(),
                "unsettled": sum(counts.get(key, 0) for key in
                    ("pending", "running", "retry_wait", "ready", "blocked"))}

    async def existing_ack(self, message):
        if not self.active or message.platform.value != "discord":
            return None
        state = await self.call("state", self.service._turn_id(message))
        if state is None:
            return None
        speaker, actor_ref = self.service.attention.resolve_speaker(message)
        decision = AttentionDecision(action=RoutingAction.RESPOND,
            speaker_entity_id=speaker, speaker_actor_ref=actor_ref, score=1.0)
        if not self.eligible(message, decision):
            return InboundResult(decision=decision.model_copy(update={
                "action": RoutingAction.BLOCK, "reason_codes": ["DURABLE_REPLY_AUTHORIZATION_REQUIRED"],
            }), duplicate=True)
        self.diagnostics.receipt(True)
        return self.ack(decision, state["status"], duplicate=True)

    @staticmethod
    def ack(decision, state, *, duplicate):
        # A transport receipt is not a social message or delivery confirmation.
        return InboundResult(decision=decision.model_copy(update={
            "reason_codes": [*decision.reason_codes, "DURABLE_REPLY_ACCEPTED",
                             "DURABLE_REPLY_" + state.upper()],
        }), duplicate=duplicate)

    async def accept(self, message, decision, request, *, duplicate):
        # Build the existing canonical records, but commit through the owned
        # messaging connection together with their job. No provider call here.
        engine = self.service.cognition
        request.metadata.update({"available_response_modalities": ["text", "silence"],
                                 "visual_expression_available": False,
                                 "visual_expression_count": 0})
        item = engine._input_message(request, engine._input_dedupe(request))
        journal = engine._journal_entry(request, JournalStage.INPUT_OBSERVED,
                                        **engine._input_journal_fields(request))
        try:
            state = await self.call("admit", request.turn_id,
                engine.store._message_values(item), engine.store._journal_values(journal))
        except Exception as exc:
            if self.service._source_invalidated(message):
                return self.service._invalidated_result(message)
            _LOG.warning("durable_reply_admission_failed turn_id=%s code=%s",
                         request.turn_id, type(exc).__name__)
            raise InboundGenerationBusy("DURABLE_REPLY_ADMISSION_RETRY") from None
        if request.metadata.get("reflection_enqueue_requested"):
            try:
                self.service.reflections.enqueue(request)
            except Exception as exc:
                # The trusted marker is already committed. The existing
                # reflection reconciler repairs this independent crash gap.
                _LOG.warning("durable_reply_reflection_deferred turn_id=%s code=%s",
                             request.turn_id, type(exc).__name__)
        self.notify()
        self.diagnostics.receipt(duplicate)
        return self.ack(decision, state["status"], duplicate=duplicate)

    def hydrate(self, claim, row):
        audit_meta = json.loads(row["metadata_json"])
        transport = audit_meta["durable_reply_transport"]
        metadata = json.loads(row["cognitive_metadata"])
        message = InboundMessage(
            platform=row["platform"], account_scope=row["account_scope"],
            conversation_id=row["conversation_id"], message_id=row["message_id"],
            native_message_id=transport.get("native_message_id"),
            transport_revision=transport["transport_revision"],
            channel_type=row["channel_type"], sender_identifiers=transport["sender_identifiers"],
            sender_display_name=audit_meta.get("sender_display_name"),
            sender_username=audit_meta.get("sender_username"),
            text=row["text"], mentioned_actor_ids=json.loads(row["mentions_json"]),
            reply_to_message_id=row["reply_to_message_id"],
            reply_to_author_identifiers=transport["reply_to_author_identifiers"],
            mentions_amora=transport["mentions_amora"], reply_to_amora=transport["reply_to_amora"],
            timestamp=row["timestamp"], privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
        )
        if (str(self.service._turn_id(message)) != claim.turn_id
                or self.service._session_ref(message) != claim.session_id
                or row["input_session"] != claim.session_id
                or row["input_speaker"] != row["speaker_entity_id"]
                or row["text"] != row["cognitive_text"]
                or metadata.get("native_message_id_hash") != self.service._hash(message.native_source_message_id)
                or metadata.get("transport_revision") != message.transport_revision
                or metadata.get("reply_outbound_id") != str(self.service._outbound_id(message))):
            raise ValueError("DURABLE_REPLY_SOURCE_MISMATCH")
        # Do not replay the transient catalog or expose internal audit IDs.
        for key in ("reply_audit_id", "reply_enqueue_requested", "reply_outbound_id",
                    "active_capability_ids", "surface_available_capability_ids",
                    "response_scoped_capability_ids", "known_sticker_perception"):
            metadata.pop(key, None)
        metadata.update({"available_response_modalities": ["text", "silence"],
                         "visual_expression_available": False, "visual_expression_count": 0,
                         "provider_total_budget_seconds": self.service.provider_total_budget_seconds,
                         "provider_attempt_budget_seconds": self.service.provider_attempt_budget_seconds})
        request = CognitiveRequest(turn_id=claim.turn_id, text=row["cognitive_text"],
            speaker_id=row["input_speaker"], target_id="ENTITY_AMORA",
            session_id=claim.session_id, privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True, metadata=metadata)
        return message, request

    def authorize(self, message, request):
        speaker, actor_ref = self.service.attention.resolve_speaker(message)
        decision = AttentionDecision(action=RoutingAction.RESPOND, score=1.0,
            speaker_entity_id=speaker, speaker_actor_ref=actor_ref,
            addressee_id="ENTITY_AMORA", reason_codes=["DURABLE_REPLY_REAUTHORIZED"])
        if (speaker != request.speaker_id or not self.eligible(message, decision)
                or self.service._source_invalidated(message)):
            return None
        return decision

    @staticmethod
    def delay(attempts):
        # Minimal RUN 01 recovery; provider-aware Retry-After/circuits are RUN 02.
        return min(300, 5 * (2 ** min(6, max(0, attempts - 1))))

    async def defer(self, claim, reason, *, retry_after_seconds=None):
        # Jitter avoids synchronized retries; never undercut a server delay.
        delay = max(1, math.ceil(self.delay(claim.attempts) * random.uniform(0.8, 1.0)))
        if retry_after_seconds is not None:
            if not math.isfinite(retry_after_seconds) or retry_after_seconds > 604800:
                return await self.call("block", claim, ReplyReason.CONFIGURATION_BLOCKED)
            delay = max(delay, math.ceil(retry_after_seconds))
        return await self.call("defer", claim, reason, delay_seconds=delay)

    async def _heartbeat(self, claim, work):
        while not work.done():
            await asyncio.sleep(30)
            if not work.done() and not await self.call("renew", claim):
                work.cancel()
                return

    async def _generate(self, claim, message, request):
        async def live_guard():
            return (self.authorize(message, request) is not None
                    and await self.call("renew", claim))
        from amora.cognition.causal_conversation import (composition_window, causal_partition,
            GROUP_RELATED_TURN, grouping_enabled)
        window = composition_window(request.text)
        age = max(0.0, (self.service.clock.now() - message.timestamp).total_seconds())
        if claim.attempts == 1 and window > age:
            await asyncio.sleep(window - age)
            if not await live_guard():
                raise DeferredRealization(["DURABLE_AUTHORIZATION_LOST"])
        checked_context = None
        async def causal_check(context):
            nonlocal checked_context
            checked_context = context
            engine = self.service.cognition
            options = {"limit":96, "session_id":request.session_id, "exclude_invalidated":True}
            lane = engine.store.async_lane
            if lane is not None:
                rows = await lane.call("list_messages", **options)
            else:
                rows = engine.store.list_messages(**options)
            _, followups, state = causal_partition(request, rows,
                allowed=lambda item: engine.compiler.privacy_gate.message_allowed(
                    item, remote=True, recipient_id=request.speaker_id))
            original_state = ("anchor_missing" if "CAUSAL_HISTORY_ANCHOR_MISSING" in context.reason_codes
                              else "partitioned")
            return state == original_state and followups == context.causal_followups

        token = DURABLE_REALIZATION.set(claim.turn_id)
        guard_token = DURABLE_AUTHORIZATION.set(live_guard)
        causal_token = DURABLE_CAUSAL_CHECK.set(causal_check)
        group_token = GROUP_RELATED_TURN.set(claim.turn_id if grouping_enabled() and claim.attempts<=3 else None)
        self.service._active_dm_progress.add(claim.turn_id)
        from amora.cognition.realtime_candidates import CANDIDATE_SINK
        bridge=self.service.realtime_bridge
        draft=None
        sink_token=None
        completed=False
        try:
            if bridge is not None and bridge.binding(message) is not None:
                draft=await bridge.begin(claim,message,request,live_guard)
                if draft is not None:
                    sink_token=CANDIDATE_SINK.set(draft)
            started = monotonic()
            deadline = started + self.service.cognition_timeout_seconds + 120.0
            total_budget = float(request.metadata["provider_total_budget_seconds"])
            for index in range(2):
                remaining = deadline - monotonic()
                if remaining <= 0:
                    raise TimeoutError()
                current = request
                if index:
                    # Conservative: charge all elapsed wall time against the
                    # original provider budget; never give replan a fresh one.
                    budget = min(remaining, total_budget - (monotonic() - started))
                    if budget < 0.25:
                        raise CausalReplanRequired()
                    current = request.model_copy(update={"metadata":{
                        **request.metadata, "provider_total_budget_seconds":budget}})
                    if not await live_guard():
                        raise DeferredRealization(["DURABLE_AUTHORIZATION_LOST"])
                try:
                    plan = await asyncio.wait_for(
                        self.service.cognition.respond_observed(current,on_segment=None,
                            fallback_text=self.service.conversation.composer.operational_provider_fallback()),
                        timeout=remaining)
                    snapshot = await self.call("causal_snapshot", claim.session_id)
                    if checked_context is not None and not await causal_check(checked_context):
                        raise CausalReplanRequired()
                    if not await live_guard():
                        raise DeferredRealization(["DURABLE_AUTHORIZATION_LOST"])
                except CausalReplanRequired:
                    if draft is not None and draft.text:
                        draft.pause()
                    if index:
                        raise
                    _LOG.info("durable_causal_replan turn_id=%s",claim.turn_id)
                    continue
                if index:
                    plan.reason_codes.append("DURABLE_CAUSAL_REPLANNED")
                plan.metadata["causal_snapshot"] = snapshot
                if GROUP_RELATED_TURN.get() == claim.turn_id and checked_context is not None:
                    members = [item["turn_id"] for item in checked_context.causal_followups
                               if item.get("independent_reply_still_pending") is False]
                    if members:
                        plan.metadata["causal_group_members"] = members
                if bridge is not None and bridge.binding(message) is not None:
                    plan.metadata["realtime_v1"]=True
                completed=True
                return plan
        finally:
            if sink_token is not None:
                CANDIDATE_SINK.reset(sink_token)
            if bridge is not None:
                bridge.end(claim.turn_id,completed=completed)
            self.service._active_dm_progress.discard(claim.turn_id)
            GROUP_RELATED_TURN.reset(group_token)
            DURABLE_CAUSAL_CHECK.reset(causal_token)
            DURABLE_AUTHORIZATION.reset(guard_token)
            DURABLE_REALIZATION.reset(token)

    def _history_records(self, request, outbound):
        # Same existing speech normalization and history model; deterministic
        # IDs and ordinal make materialization replay-safe, with no TTS callback.
        engine = self.service.cognition
        request = request.model_copy(update={"metadata": {
            **request.metadata, "response_display_text": outbound.text}})
        segmenter = SpeechSegmenter()
        records = []
        journals = []
        for raw in [*segmenter.feed(outbound.text), *segmenter.flush()]:
            built = engine._build_segment_record(request, raw, len(records),
                outbound.metadata.get("provider"), outbound.metadata.get("model"))
            if built is None:
                continue
            segment, message, fields = built
            segment.id = uuid5(NAMESPACE_URL, f"amora:durable-segment:{request.turn_id}:{segment.ordinal}")
            message.id = uuid5(NAMESPACE_URL, f"amora:durable-history:{request.turn_id}:{segment.ordinal}")
            segment.queued_at = outbound.created_at
            message.started_at = outbound.created_at
            segment.metadata["message_id"] = str(message.id)
            message.metadata["segment_id"] = str(segment.id)
            message.metadata.update(engine._segment_execution_metadata(request, None, segment.speech_text))
            records.append((engine.store._segment_values(segment), engine.store._message_values(message)))
            fields["payload"].update({"segment_id": str(segment.id), "message_id": str(message.id)})
            entry = engine._journal_entry(request, JournalStage.SPEECH_SEGMENT_READY,
                **fields, dedupe_key=f"journal:durable-segment:{request.turn_id}:{segment.ordinal}")
            journals.append(engine.store._journal_values(entry))
        if not records:
            raise ValueError("DURABLE_REPLY_EMPTY_HISTORY")
        for stage, summary, dedupe in (
            (JournalStage.RESPONSE_PLANNED, "durable validated response ready", "response-planned"),
            (JournalStage.TURN_FINISHED, "durable cognitive turn ready for delivery", "turn-finished"),
        ):
            entry = engine._journal_entry(request, stage, summary=summary,
                provider=outbound.metadata.get("provider"), model=outbound.metadata.get("model"),
                reason_codes=["DURABLE_REPLY_READY"], payload={"segments": len(records)},
                dedupe_key=f"journal:{dedupe}:{request.turn_id}")
            journals.append(engine.store._journal_values(entry))
        return records, journals

    async def execute(self, claim):
        try:
            row = await self.call("load", claim)
            if row is None:
                await self.call("block", claim, ReplyReason.AUTHORIZATION_REVOKED)
                return
            try:
                message, request = self.hydrate(claim, row)
            except (KeyError, TypeError, ValueError, ValidationError):
                await self.call("block", claim, ReplyReason.CONFIGURATION_BLOCKED)
                return
            decision = self.authorize(message, request)
            if decision is None:
                await self.call("block", claim, ReplyReason.AUTHORIZATION_REVOKED)
                return
            prepared = await self.call("prepared", claim.turn_id, claim.token)
            if prepared is None:
                await self.service.cognition.record_turn_decision_async(request,
                    "TAKE_TURN", ["DURABLE_REPLY_REAUTHORIZED"], 1.0)
                if claim.attempts == 1:
                    appraise = getattr(self.service.conversation, "_appraise_interaction", None)
                    if callable(appraise):
                        await appraise(request.text, speaker_id=request.speaker_id,
                            addressee_id="ENTITY_AMORA", source_event_id=request.turn_id,
                            privacy_scope=request.privacy_scope, metadata=request.metadata)
                initial_snapshot = await self.call("causal_snapshot", claim.session_id)
                plan = await self._generate(claim, message, request)
                if (plan.fallback_used or not plan.metadata.get("realization_validated")
                        or not plan.display_text.strip()):
                    raise DeferredRealization(plan.reason_codes or ["EMPTY_PROVIDER_OUTPUT"])
                decision = self.authorize(message, request)
                if decision is None:
                    await self.call("block", claim, ReplyReason.AUTHORIZATION_REVOKED)
                    return
                reasons = ["CORE_OUTBOUND_ENQUEUED", *plan.reason_codes]
                outbound = self.service._build_outbound(message, decision, plan.display_text,
                    metadata={"cognitive_turn_id": claim.turn_id,
                              "generation_stage": "realization", "generation_error_code": None,
                              "generation_latency_ms": plan.latency_ms,
                              "provider": plan.provider, "model": plan.model, "fallback_used": False,
                              "cognitive_reason_codes": plan.reason_codes,
                              "remote_processing_allowed": True, "transport_revision": message.transport_revision,
                              "delivery_message_id": message.message_id,
                              "durable_native_hash": self.service._hash(message.native_source_message_id),
                              "causal_snapshot": plan.metadata.get("causal_snapshot",initial_snapshot),
                              **({"causal_group_members": plan.metadata["causal_group_members"]}
                                 if plan.metadata.get("causal_group_members") else {}),
                              **({"realtime_v1":True} if plan.metadata.get("realtime_v1") else {})})
                if not await self.call("stage", claim.turn_id, claim.token, outbound, decision, reasons):
                    raise InboundGenerationLost("DURABLE_REPLY_LEASE_LOST")
            else:
                outbound, _, reasons = prepared
                if str(outbound.id) != str(self.service._outbound_id(message)):
                    await self.call("block", claim, ReplyReason.CONFIGURATION_BLOCKED)
                    return
            # Reauthorize even when the provider result came from a checkpoint.
            decision = self.authorize(message, request)
            if decision is None:
                await self.call("block", claim, ReplyReason.AUTHORIZATION_REVOKED)
                return
            records, journals = self._history_records(request, outbound)
            finalized = await self.call("finalize", claim, message, decision, outbound, reasons, records, journals)
            if finalized is None:
                _LOG.info("durable_causal_checkpoint_changed turn_id=%s",claim.turn_id)
                await self.defer(claim,ReplyReason.STORAGE_TEMPORARY)
                return
            if not finalized:
                raise InboundGenerationLost("DURABLE_REPLY_FINALIZE_LOST")
            _LOG.info("durable_reply_ready turn_id=%s attempts=%d", claim.turn_id, claim.attempts)
        except DeferredRealization as exc:
            if "CONTEXT_ENVELOPE_OVERFLOW" in exc.reasons:
                await self.call("block", claim, ReplyReason.CONTEXT_BLOCKED)
            elif "DURABLE_AUTHORIZATION_LOST" in exc.reasons:
                await self.call("cancel_invalidated")
                await self.call("block", claim, ReplyReason.AUTHORIZATION_REVOKED)
            elif any(reason in {"DURABLE_CALLBACK_UNSUPPORTED",
                                "CONFIDENTIAL_OUTPUT_SUPPRESSED", "PROVIDER_CONFIGURATION_BLOCKED"} for reason in exc.reasons):
                await self.call("block", claim, ReplyReason.CONFIGURATION_BLOCKED)
            else:
                await self.defer(claim, ReplyReason.PROVIDER_BACKOFF if exc.retry_after_seconds is not None
                    else ReplyReason.PROVIDER_TEMPORARY, retry_after_seconds=exc.retry_after_seconds)
        except TimeoutError:
            await self.defer(claim, ReplyReason.GENERATION_TIMEOUT)
        except InboundGenerationLost:
            # A stale owner must never unconditionally release or re-enqueue.
            await self.call("cancel_invalidated")
        except asyncio.CancelledError:
            # Shutdown leaves durable lease/prepare for the next process.
            raise
        except Exception as exc:
            _LOG.warning("durable_reply_error turn_id=%s stage=execute code=%s",
                         claim.turn_id, type(exc).__name__)
            if claim.attempts >= 5:
                await self.call("block", claim, ReplyReason.CONFIGURATION_BLOCKED)
            else:
                await self.defer(claim, ReplyReason.STORAGE_TEMPORARY)

    async def _execute_observed(self, claim):
        from amora.resource_metrics import resource_sample,resource_delta
        from amora.latency_trace import bind_latency_trace,log_trace
        with bind_latency_trace() as trace:
            before=resource_sample()
            try:
                await self._execute_observed_inner(claim)
            finally:
                log_trace("durable_reply_resources",resource_delta(before,resource_sample()),turn_id=claim.turn_id)
                log_trace("durable_reply",trace,turn_id=claim.turn_id)

    async def _execute_observed_inner(self,claim):
        started = monotonic()
        outcome, reason = "unknown", None
        try:
            await self.execute(claim)
            try:
                state = await self.call("state", claim.turn_id)
                if state:
                    outcome, reason = state["status"], state["last_reason"]
            except Exception:
                # Diagnostics must never alter a committed reply outcome.
                pass
        except asyncio.CancelledError:
            outcome = "caller_cancelled"
            raise
        finally:
            self.diagnostics.attempt(claim.turn_id, claim.attempts, outcome, reason,
                                     (monotonic() - started) * 1000)

    async def run_once(self):
        if not self.active:
            return False
        async with self._execution:
            await self.call("cancel_invalidated")
            await self.call("reconcile_outbound")
            now = monotonic()
            if now >= self._next_reconcile:
                await self.call("reconcile_missing")
                self._next_reconcile = now + 120
            claim = await self.call("claim")
            if claim is None:
                return False
            work = asyncio.create_task(self._execute_observed(claim))
            heartbeat = asyncio.create_task(self._heartbeat(claim, work))
            try:
                done, _ = await asyncio.wait({work, heartbeat}, return_when=asyncio.FIRST_COMPLETED)
                if heartbeat in done and not work.done():
                    work.cancel()
                if work in done:
                    await work
                elif heartbeat in done:
                    await heartbeat
            finally:
                for task in (work, heartbeat):
                    if not task.done():
                        task.cancel()
                await asyncio.gather(work, heartbeat, return_exceptions=True)
            return True

    async def _loop(self):
        while self.active:
            self.wake.clear()
            try:
                if await self.run_once():
                    continue
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                _LOG.warning("durable_reply_poll_failed code=%s", type(exc).__name__)
            with suppress(TimeoutError):
                await asyncio.wait_for(self.wake.wait(), timeout=5.0)
