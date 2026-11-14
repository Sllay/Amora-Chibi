from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from contextvars import ContextVar
from uuid import NAMESPACE_URL, UUID, uuid4, uuid5

from amora.cognition import CognitiveRequest
from amora.memory.recall_intent import parse_recall_intent
from .memory_disclosure import parse_disclosure_intent, disclosure_excerpt

from .attention import SocialAttentionRouter
from .disclosure import public_text_allowed, audience_text_allowed
from .models import (
    AttentionDecision,
    AuditDirection,
    ChannelType,
    InboundMessage,
    InboundResult,
    MessagingAuditRecord,
    OutboundMessage,
    RoutingAction,
    TransportInvalidation,
)
from .reflection_queue import ReflectionQueue
from .explicit_memory import (
    OWNER_ENTITY_ID, PROVENANCE_SOURCE, is_verified_owner, parse_explicit_memory,
)
from .generation_lifecycle import InboundGeneration, InboundGenerationBusy, InboundGenerationLost
from .presence import PlatformPresenceController
from .presence_control import PlatformPresenceControlService, PresenceControlOutcome
from .store import MessagingStore


_LOG = logging.getLogger("amora.messaging")
_GENERATION_LEASE: ContextVar[tuple[str, str] | None] = ContextVar("amora_generation_lease", default=None)
# Discord uses a synchronous inbound request until durable background generation lands.
# Give the social model >=60s while leaving room for failover, context and delivery.
# Ladder: provider attempt < provider total < cognition < worker HTTP < Core HTTP.
_MESSAGING_PROVIDER_TOTAL_BUDGET_SECONDS = 90.0
_MESSAGING_PROVIDER_ATTEMPT_BUDGET_SECONDS = 75.0
_MESSAGING_COGNITION_TIMEOUT_SECONDS = 105.0
_TERMINAL_DUPLICATE_REASONS = frozenset(
    {
        "REPLY_LEASE_INVALIDATED",
        "EMPTY_REALIZATION_SUPPRESSED",
        "CONFIDENTIAL_OUTPUT_SUPPRESSED",
    }
)


class PlatformMessagingService:
    """Common messaging pipeline between platform workers and Cognitive Core.

    Platform workers normalize transport facts. Core owns identity resolution,
    DM authorization, group attention, cognition, presence-control authority and
    outbound intent. A logical platform message owns one deterministic cognitive
    turn and one deterministic outbound reply, so request retry/redeploy cannot
    create a second logical answer.
    """

    def __init__(
        self,
        *,
        store: MessagingStore,
        attention: SocialAttentionRouter,
        cognition,
        conversation,
        clock,
        presence_control: PlatformPresenceControlService | None = None,
        cognition_timeout_seconds: float = _MESSAGING_COGNITION_TIMEOUT_SECONDS,
        provider_total_budget_seconds: float = _MESSAGING_PROVIDER_TOTAL_BUDGET_SECONDS,
        provider_attempt_budget_seconds: float = _MESSAGING_PROVIDER_ATTEMPT_BUDGET_SECONDS,
    ) -> None:
        self.store = store
        self.attention = attention
        self.cognition = cognition
        self.conversation = conversation
        self.clock = clock
        self.cognition_timeout_seconds = max(1.0, float(cognition_timeout_seconds))
        self.provider_total_budget_seconds = max(0.1, float(provider_total_budget_seconds))
        self.provider_attempt_budget_seconds = max(0.1, float(provider_attempt_budget_seconds))
        if self.provider_total_budget_seconds >= self.cognition_timeout_seconds:
            raise ValueError("provider total budget must be below messaging cognition timeout")
        self.presence_control = presence_control or PlatformPresenceControlService(
            profiles=attention.profiles,
            presence=PlatformPresenceController(store, clock, reset_transient=False),
        )
        self._inflight: dict[str, asyncio.Task[InboundResult]] = {}
        self.reflections = ReflectionQueue(store.database, cognition, clock)
        self.generations = InboundGeneration(store.database, clock)

    async def receive(self, message: InboundMessage) -> InboundResult:
        """Process one logical inbound exactly once per Core process.

        HTTP client cancellation must not cancel the logical turn. A retry in the
        same process joins the same shielded task; a retry after process replacement
        is recovered from deterministic audit/turn/outbound identifiers.
        """
        key = self._inbound_key(message)
        task = self._inflight.get(key)
        if task is None or task.done():
            task = asyncio.create_task(
                self._receive_owned(message),
                name=f"amora-messaging-{self._hash(key)}",
            )
            self._inflight[key] = task
            task.add_done_callback(lambda done, key=key: self._drop_inflight(key, done))
        return await asyncio.shield(task)

    def _source_invalidated(self, message):
        return self.reflections.source_invalidated(
            self._session_ref(message), self._hash(message.native_source_message_id),
            message.transport_revision,
        )

    async def _receive_owned(self, message: InboundMessage) -> InboundResult:
        from amora.events import PrivacyScope
        if message.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            return await self._receive_once(message)
        turn_id = str(self._turn_id(message))
        token = str(uuid4())
        deadline = asyncio.get_running_loop().time() + min(110, self.cognition_timeout_seconds + 5)
        while not self.generations.claim(
            turn_id, self._session_ref(message), self._hash(message.native_source_message_id),
            message.transport_revision, token,
        ):
            if asyncio.get_running_loop().time() >= deadline:
                raise InboundGenerationBusy("INBOUND_GENERATION_BUSY")
            await asyncio.sleep(0.05)
        context_token = _GENERATION_LEASE.set((turn_id, token))
        completed = False
        try:
            result = await self._receive_once(message)
            completed = True
            return result
        except InboundGenerationLost:
            if self._source_invalidated(message):
                completed = True
                return self._invalidated_result(message)
            raise InboundGenerationBusy("INBOUND_GENERATION_LEASE_LOST") from None
        finally:
            _GENERATION_LEASE.reset(context_token)
            self.generations.release(turn_id, token, completed=completed)

    def _invalidated_result(self, message):
        audit = self.store.get_audit(platform=message.platform, account_scope=message.account_scope,
                                     conversation_id=message.conversation_id, message_id=message.message_id)
        if audit is not None:
            self.store.add_inbound_reason(platform=message.platform, account_scope=message.account_scope,
                                          conversation_id=message.conversation_id, message_id=message.message_id,
                                          reason="REPLY_LEASE_INVALIDATED")
        speaker, actor_ref = self.attention.resolve_speaker(message)
        return InboundResult(decision=AttentionDecision(
            action=RoutingAction.SILENCE, speaker_entity_id=speaker, speaker_actor_ref=actor_ref,
            reason_codes=["REPLY_LEASE_INVALIDATED"]), duplicate=audit is not None)

    def _drop_inflight(self, key: str, task: asyncio.Task[InboundResult]) -> None:
        if self._inflight.get(key) is task:
            self._inflight.pop(key, None)

    async def _receive_once(self, message: InboundMessage) -> InboundResult:
        from amora.events import PrivacyScope
        if message.privacy_scope in {PrivacyScope.AUTH, PrivacyScope.SECRET}:
            return InboundResult(decision=AttentionDecision(
                action=RoutingAction.BLOCK, speaker_actor_ref="protected",
                reason_codes=["PRIVACY_SCOPE_BLOCKED"]))
        if self._source_invalidated(message):
            return self._invalidated_result(message)
        lease = _GENERATION_LEASE.get()
        prepared = self.generations.prepared(*lease) if lease is not None else None
        if prepared is not None:
            outbound, decision, reasons = prepared
            if outbound.metadata.get("memory_disclosure") and not self._disclosure_replay_allowed(message, decision, outbound):
                self.generations.invalidate_prepared(
                    self._session_ref(message), self._hash(message.native_source_message_id), message.transport_revision)
                return self._invalidated_result(message)
            if message.channel_type == ChannelType.GROUP and (
                not self.attention.floor.validate_reply(message, decision)
                or not public_text_allowed(outbound.text)
                or not audience_text_allowed(outbound.text, audience_id=self._session_ref(message))
            ):
                return self._invalidated_result(message)
            outbound = self._persist_prepared_outbound(message, decision, outbound, reasons)
            return InboundResult(decision=decision, duplicate=True, outbound=[outbound])
        existing_audit = self.store.get_audit(
            platform=message.platform,
            account_scope=message.account_scope,
            conversation_id=message.conversation_id,
            message_id=message.message_id,
        )
        if existing_audit is not None:
            duplicate_result = self._completed_duplicate_result(message, existing_audit)
            if duplicate_result is not None:
                self._log_result(duplicate_result)
                return duplicate_result
            if existing_audit.routing_action == RoutingAction.CONTEXT_ONLY:
                _, actor_ref = self.attention.resolve_speaker(message)
                decision = AttentionDecision(
                    action=RoutingAction.CONTEXT_ONLY, speaker_entity_id=existing_audit.speaker_entity_id,
                    speaker_actor_ref=actor_ref, reason_codes=["DUPLICATE_RECOVERY_RESUME", "MESSAGING_CONTEXT_ONLY"],
                )
            else:
                routed = self.attention.route(message)
                decision = routed.model_copy(
                    update={"reason_codes": [*routed.reason_codes, "DUPLICATE_RECOVERY_RESUME"]}
                )
            duplicate_recovery = True
        else:
            decision = self.attention.route(message)
            duplicate_recovery = False
            first_external = message.sender_identifiers[0].external_actor_id
            inbound_audit = MessagingAuditRecord(
                platform=message.platform,
                account_scope=message.account_scope,
                conversation_id=message.conversation_id,
                message_id=message.message_id,
                direction=AuditDirection.INBOUND,
                channel_type=message.channel_type,
                sender_external_id=first_external,
                speaker_entity_id=decision.speaker_entity_id,
                text=message.text,
                reply_to_message_id=message.reply_to_message_id,
                mentions=list(message.mentioned_actor_ids),
                routing_action=decision.action,
                reason_codes=list(decision.reason_codes),
                responded=False,
                timestamp=message.timestamp,
                metadata={
                    "sender_display_name": message.sender_display_name,
                    "sender_username": message.sender_username,
                    "mentions_amora": message.mentions_amora,
                    "reply_to_amora": message.reply_to_amora,
                    "attachment_count": len(message.attachments),
                    "floor_generation": decision.floor_generation,
                    "native_message_id_hash": self._hash(message.native_source_message_id),
                    "transport_revision": message.transport_revision,
                    "deterministic_turn_id": str(self._turn_id(message)),
                    "deterministic_outbound_id": str(self._outbound_id(message)),
                },
            )
            if not self.store.append_audit(inbound_audit):
                raced = self.store.get_audit(
                    platform=message.platform,
                    account_scope=message.account_scope,
                    conversation_id=message.conversation_id,
                    message_id=message.message_id,
                )
                if raced is not None:
                    duplicate_result = self._completed_duplicate_result(message, raced)
                    if duplicate_result is not None:
                        self._log_result(duplicate_result)
                        return duplicate_result
                decision = decision.model_copy(
                    update={"reason_codes": [*decision.reason_codes, "AUDIT_DEDUPE_RACE_RECOVERY"]}
                )
                duplicate_recovery = True

        actor_id = decision.speaker_entity_id or decision.speaker_actor_ref
        cognitive_text = self._cognitive_text(message)

        if decision.action in {RoutingAction.BLOCK, RoutingAction.QUARANTINE, RoutingAction.SILENCE}:
            result = InboundResult(decision=decision, duplicate=duplicate_recovery)
            self._log_result(result)
            return result

        explicit_memory = (parse_explicit_memory(message.text)
                           if decision.action == RoutingAction.RESPOND else None)
        disclosure_intent = (parse_disclosure_intent(message.text)
                             if decision.action == RoutingAction.RESPOND and explicit_memory is None else None)
        recall_intent = (parse_recall_intent(message.text)
                         if decision.action == RoutingAction.RESPOND and explicit_memory is None
                         and disclosure_intent is None else None)
        if recall_intent is not None and not is_verified_owner(
                self.attention.profiles, message, decision.speaker_entity_id):
            recall_intent = None
        if (decision.action == RoutingAction.RESPOND and explicit_memory is None
                and recall_intent is None and disclosure_intent is None):
            control = self.presence_control.handle(
                message.text,
                speaker_entity_id=decision.speaker_entity_id,
                account_scope=message.account_scope,
            )
            if control is not None:
                result = self._presence_control_result(message, decision, control)
                result = result.model_copy(update={"duplicate": duplicate_recovery})
                self._log_result(result, provider="local_control", fallback_used=False)
                return result

        request = CognitiveRequest(
            text=cognitive_text,
            speaker_id=actor_id,
            target_id=("ENTITY_AMORA" if decision.action == RoutingAction.RESPOND else None),
            source_event_id=None,
            session_id=self._session_ref(message),
            privacy_scope=message.privacy_scope,
            remote_processing_allowed=message.remote_processing_allowed,
            turn_id=self._turn_id(message),
            metadata={
                "surface": "platform_messaging",
                "platform": message.platform.value,
                "channel_type": message.channel_type.value,
                "conversation_ref": self._conversation_ref(message),
                "platform_message_id_hash": self._hash(message.message_id),
                "native_message_id_hash": self._hash(message.native_source_message_id),
                "transport_revision": message.transport_revision,
                "social_attention_score": decision.score,
                "social_attention_reasons": list(decision.reason_codes),
                "floor_generation": decision.floor_generation,
                "memory_consolidation": message.remote_processing_allowed,
                "observed_only": decision.action == RoutingAction.CONTEXT_ONLY,
                "relationship_learning": False,
                "messaging_audit": True,
                "provider_total_budget_seconds": self.provider_total_budget_seconds,
                "provider_attempt_budget_seconds": self.provider_attempt_budget_seconds,
            },
        )
        if explicit_memory is not None:
            # The direct request owns its own receipt, so neither the provider
            # nor the automatic reflection queue can claim/duplicate its write.
            request.metadata["explicit_memory_request"] = True
            request.metadata["reflection_enqueue_requested"] = False
            request.metadata["memory_consolidation"] = False
            self.cognition.observe_input(request)
            result = self._explicit_memory_result(message, decision, request, explicit_memory)
            result = result.model_copy(update={"duplicate": duplicate_recovery})
            self._log_result(result, provider="local_control", fallback_used=False,
                             cognitive_reason_codes=list(result.outbound[0].metadata.get("cognitive_reason_codes", []))
                             if result.outbound else [],
                             remote_processing_allowed=message.remote_processing_allowed)
            return result

        if disclosure_intent is not None:
            request.metadata["memory_disclosure_request"] = True
            request.metadata["reflection_enqueue_requested"] = False
            request.metadata["memory_consolidation"] = False
            self.cognition.observe_input(request)
            result = self._memory_disclosure_result(message, decision, request, disclosure_intent)
            result = result.model_copy(update={"duplicate": duplicate_recovery})
            self._log_result(result, provider="local_control", fallback_used=False,
                             cognitive_reason_codes=list(result.outbound[0].metadata.get("cognitive_reason_codes", []))
                             if result.outbound else [],
                             remote_processing_allowed=message.remote_processing_allowed)
            return result

        if recall_intent is not None:
            request.metadata["explicit_memory_recall"] = True
            request.metadata["reflection_enqueue_requested"] = False
            request.metadata["memory_consolidation"] = False
            self.cognition.observe_input(request)
            result = self._explicit_recall_result(message, decision, request, recall_intent)
            result = result.model_copy(update={"duplicate": duplicate_recovery})
            self._log_result(result, provider="local_control", fallback_used=False,
                             cognitive_reason_codes=list(result.outbound[0].metadata.get("cognitive_reason_codes", []))
                             if result.outbound else [],
                             remote_processing_allowed=message.remote_processing_allowed)
            return result

        enqueue_reflection = message.remote_processing_allowed and (
            decision.action == RoutingAction.RESPOND or (
                decision.action == RoutingAction.CONTEXT_ONLY
                and decision.speaker_entity_id is not None
                and os.getenv("AMORA_REFLECTION_OBSERVED", "0") == "1"
            )
        )
        # Persist the intent with the input. The queue reconciles the crash
        # window between these separate writes, without importing old history.
        request.metadata["reflection_enqueue_requested"] = enqueue_reflection
        self.cognition.observe_input(request)
        if enqueue_reflection:
            self.reflections.enqueue(request)

        if decision.action == RoutingAction.CONTEXT_ONLY:
            self.cognition.record_turn_decision(
                request,
                "LISTEN",
                [*decision.reason_codes, "MESSAGING_CONTEXT_ONLY"],
                decision.score,
            )
            result = InboundResult(decision=decision, duplicate=duplicate_recovery)
            self._log_result(result, remote_processing_allowed=message.remote_processing_allowed)
            return result

        if decision.action != RoutingAction.RESPOND:
            result = InboundResult(decision=decision, duplicate=duplicate_recovery)
            self._log_result(result, remote_processing_allowed=message.remote_processing_allowed)
            return result

        self.cognition.record_turn_decision(
            request,
            "TAKE_TURN",
            [*decision.reason_codes, "MESSAGING_REPLY_AUTHORIZED"],
            decision.score,
        )

        appraise = getattr(self.conversation, "_appraise_interaction", None)
        if callable(appraise):
            await appraise(
                cognitive_text,
                speaker_id=actor_id,
                addressee_id="ENTITY_AMORA",
                source_event_id=request.turn_id,
                privacy_scope=message.privacy_scope,
                metadata=request.metadata,
            )

        # Remote generation failures must not masquerade as a social acknowledgement.
        # Local-only conversation retains its legitimate local composition.
        fallback = (
            self.conversation.composer.operational_provider_fallback()
            if message.remote_processing_allowed
            else self.conversation.composer.fallback_dialogue(cognitive_text, speaker_id=actor_id)
        )
        provider: str | None = None
        model: str | None = None
        fallback_used = False
        cognitive_reasons: list[str] = []
        cognitive_turn_id = str(request.turn_id)
        generation_started = self.clock.monotonic()
        try:
            plan = await asyncio.wait_for(
                self.cognition.respond(request, on_segment=None, fallback_text=fallback),
                timeout=self.cognition_timeout_seconds,
            )
            provider = plan.provider
            model = plan.model
            fallback_used = bool(plan.fallback_used)
            cognitive_reasons = list(plan.reason_codes)
            cognitive_turn_id = str(plan.turn_id)
            text = (plan.display_text or plan.speech_text or fallback).strip()
            if "CONFIDENTIAL_OUTPUT_SUPPRESSED" in cognitive_reasons:
                text = ""
            elif message.channel_type != ChannelType.GROUP or (
                public_text_allowed(text) and public_text_allowed(plan.speech_text or "")
                and audience_text_allowed(text, audience_id=request.session_id)
                and audience_text_allowed(plan.speech_text or "", audience_id=request.session_id)
            ):
                await self.cognition.emit_event(plan)
            else:
                text = ""
                cognitive_reasons.append("CONFIDENTIAL_OUTPUT_SUPPRESSED")
        except TimeoutError:
            fallback_used = True
            cognitive_reasons = [
                "MESSAGING_COGNITION_HARD_TIMEOUT",
                "LOCAL_FALLBACK",
                "OPERATIONAL_TIMEOUT_FALLBACK",
            ]
            text = self.conversation.composer.operational_timeout_fallback().strip()
            self.store.add_inbound_reason(
                platform=message.platform,
                account_scope=message.account_scope,
                conversation_id=message.conversation_id,
                message_id=message.message_id,
                reason="MESSAGING_COGNITION_HARD_TIMEOUT",
            )
            _LOG.warning(
                "messaging_cognition_timeout timeout_seconds=%.1f remote_processing_allowed=%s",
                self.cognition_timeout_seconds,
                message.remote_processing_allowed,
            )

        if message.channel_type == ChannelType.GROUP and not self.attention.floor.validate_reply(message, decision):
            self.store.add_inbound_reason(
                platform=message.platform,
                account_scope=message.account_scope,
                conversation_id=message.conversation_id,
                message_id=message.message_id,
                reason="REPLY_LEASE_INVALIDATED",
            )
            result = InboundResult(
                decision=decision.model_copy(
                    update={
                        "action": RoutingAction.SILENCE,
                        "reason_codes": [*decision.reason_codes, "REPLY_LEASE_INVALIDATED"],
                    }
                ),
                duplicate=duplicate_recovery,
            )
            self._log_result(
                result,
                provider=provider,
                fallback_used=fallback_used,
                cognitive_reason_codes=cognitive_reasons,
                remote_processing_allowed=message.remote_processing_allowed,
            )
            return result

        if message.channel_type == ChannelType.GROUP and not (public_text_allowed(text) and audience_text_allowed(text, audience_id=request.session_id)):
            text = ""
            cognitive_reasons.append("CONFIDENTIAL_OUTPUT_SUPPRESSED")

        if not text:
            suppression_reason = ("CONFIDENTIAL_OUTPUT_SUPPRESSED"
                                  if "CONFIDENTIAL_OUTPUT_SUPPRESSED" in cognitive_reasons
                                  else "EMPTY_REALIZATION_SUPPRESSED")
            self.store.add_inbound_reason(
                platform=message.platform,
                account_scope=message.account_scope,
                conversation_id=message.conversation_id,
                message_id=message.message_id,
                reason=suppression_reason,
            )
            result = InboundResult(
                decision=decision.model_copy(
                    update={
                        "reason_codes": [*decision.reason_codes, suppression_reason],
                    }
                ),
                duplicate=duplicate_recovery,
            )
            self._log_result(
                result,
                provider=provider,
                fallback_used=fallback_used,
                cognitive_reason_codes=cognitive_reasons,
                remote_processing_allowed=message.remote_processing_allowed,
            )
            return result

        outbound = self._queue_outbound(
            message,
            decision,
            text,
            metadata={
                "cognitive_turn_id": cognitive_turn_id,
                "generation_latency_ms": max(0, round((self.clock.monotonic() - generation_started) * 1000)),
                "generation_stage": ("local" if not message.remote_processing_allowed else "fallback" if fallback_used else "realization"),
                "generation_error_code": cognitive_reasons[0] if message.remote_processing_allowed and fallback_used and cognitive_reasons else None,
                "provider": provider,
                "model": model,
                "fallback_used": fallback_used,
                "cognitive_reason_codes": cognitive_reasons,
                "remote_processing_allowed": message.remote_processing_allowed,
                "floor_generation": decision.floor_generation,
                "attention_score": decision.score,
                "transport_revision": message.transport_revision,
                "delivery_message_id": message.message_id,
            },
            reason_codes=["CORE_OUTBOUND_ENQUEUED", *cognitive_reasons],
        )
        result = InboundResult(decision=decision, duplicate=duplicate_recovery, outbound=[outbound])
        self._log_result(
            result,
            provider=provider,
            fallback_used=fallback_used,
            cognitive_reason_codes=cognitive_reasons,
            remote_processing_allowed=message.remote_processing_allowed,
        )
        return result

    def _completed_duplicate_result(
        self,
        message: InboundMessage,
        audit: MessagingAuditRecord,
    ) -> InboundResult | None:
        outbound = self.store.get_outbound(self._outbound_id(message))
        _, actor_ref = self.attention.resolve_speaker(message)
        reasons = list(audit.reason_codes)
        if outbound is not None:
            decision = AttentionDecision(
                action=RoutingAction.RESPOND,
                score=1.0,
                speaker_entity_id=audit.speaker_entity_id,
                speaker_actor_ref=actor_ref,
                addressee_id="ENTITY_AMORA",
                reason_codes=[*reasons, "DUPLICATE_OUTBOUND_REUSED"],
            )
            if outbound.metadata.get("memory_disclosure") and not self._disclosure_replay_allowed(message, decision, outbound):
                self.store.cancel_outbound(outbound.id, reason="MEMORY_DISCLOSURE_STALE")
                return self._invalidated_result(message)
            return InboundResult(decision=decision, duplicate=True, outbound=[outbound])

        terminal_reason = next((reason for reason in reasons if reason in _TERMINAL_DUPLICATE_REASONS), None)
        action = RoutingAction.SILENCE if terminal_reason is not None else audit.routing_action
        if action == RoutingAction.CONTEXT_ONLY and self.cognition.store.get_message_by_dedupe(
                f"cognitive-input:{self._turn_id(message)}") is None:
            return None
        if action is not None and action != RoutingAction.RESPOND:
            return InboundResult(
                decision=AttentionDecision(
                    action=action,
                    score=0.0,
                    speaker_entity_id=audit.speaker_entity_id,
                    speaker_actor_ref=actor_ref,
                    reason_codes=[*reasons, "DUPLICATE_TERMINAL_REUSED"],
                ),
                duplicate=True,
            )
        if audit.responded:
            return InboundResult(
                decision=AttentionDecision(
                    action=RoutingAction.SILENCE,
                    score=0.0,
                    speaker_entity_id=audit.speaker_entity_id,
                    speaker_actor_ref=actor_ref,
                    reason_codes=[*reasons, "DUPLICATE_RESPONDED_WITHOUT_OUTBOUND"],
                ),
                duplicate=True,
            )
        return None

    def _presence_control_result(
        self,
        message: InboundMessage,
        decision: AttentionDecision,
        control: PresenceControlOutcome,
    ) -> InboundResult:
        reason = (
            "PLATFORM_PRESENCE_CONTROL_EXECUTED"
            if control.authorized and control.executed
            else "PLATFORM_PRESENCE_CONTROL_STATUS"
            if control.authorized
            else "PLATFORM_PRESENCE_CONTROL_DENIED"
        )
        self.store.add_inbound_reason(
            platform=message.platform,
            account_scope=message.account_scope,
            conversation_id=message.conversation_id,
            message_id=message.message_id,
            reason=reason,
        )

        if message.channel_type == ChannelType.GROUP and not self.attention.floor.validate_reply(message, decision):
            return InboundResult(
                decision=decision.model_copy(
                    update={
                        "action": RoutingAction.SILENCE,
                        "reason_codes": [*decision.reason_codes, "REPLY_LEASE_INVALIDATED"],
                    }
                )
            )

        outbound = self._queue_outbound(
            message,
            decision,
            control.response_text,
            metadata={
                "local_control": True,
                "presence_control_action": control.intent.action.value,
                "presence_control_platform": control.intent.platform.value,
                "presence_control_authorized": control.authorized,
                "presence_control_executed": control.executed,
                "transport_revision": message.transport_revision,
                "delivery_message_id": message.message_id,
            },
            reason_codes=[reason, "LOCAL_DETERMINISTIC_CONTROL_REPLY"],
        )
        return InboundResult(decision=decision, outbound=[outbound])

    def _disclosure_replay_allowed(self, message, decision, outbound) -> bool:
        return (
            message.channel_type == ChannelType.GROUP
            and is_verified_owner(self.attention.profiles, message, decision.speaker_entity_id)
            and outbound.status.value != "cancelled"
            and self.store.disclosure_is_current(outbound)
            and public_text_allowed(outbound.text)
            and audience_text_allowed(outbound.text, audience_id=self._session_ref(message))
        )

    def _memory_disclosure_result(self, message, decision, request, intent) -> InboundResult:
        from datetime import timedelta
        from amora.events import PrivacyScope

        grant = None
        if message.channel_type != ChannelType.GROUP:
            reason = "MEMORY_DISCLOSURE_GROUP_REQUIRED"
            text = "Para compartilhar uma lembrança aqui, faça o pedido no canal de destino."
        elif not self.attention.floor.validate_reply(message, decision):
            return self._invalidated_result(message)
        elif not is_verified_owner(self.attention.profiles, message, decision.speaker_entity_id):
            reason = "MEMORY_DISCLOSURE_AUTHORITY_REJECTED"
            text = "Não posso compartilhar lembranças pessoais por esse pedido."
        elif message.privacy_scope not in {PrivacyScope.PERSONAL, PrivacyScope.PUBLIC, PrivacyScope.SOCIAL}:
            reason = "MEMORY_DISCLOSURE_PRIVACY_BLOCKED"
            text = "Não posso compartilhar uma lembrança com essa classificação de privacidade."
        elif not intent.valid:
            reason = "MEMORY_DISCLOSURE_NEEDS_TOPIC"
            text = "Indique uma lembrança específica que você quer compartilhar neste canal."
        else:
            memory_store = self.cognition.compiler.memory.store
            if intent.record_id:
                record = memory_store.shareable_owner_record(OWNER_ENTITY_ID, intent.record_id)
                records, complete = ([record] if record else []), True
            else:
                records, complete = memory_store.recall_owner_records(
                    OWNER_ENTITY_ID, intent.topic, limit=2, share_only=True)
            if not complete or len(records) > 1:
                reason = "MEMORY_DISCLOSURE_AMBIGUOUS"
                text = ("Preciso de uma lembrança mais específica. Confira no privado qual registro quer "
                        "compartilhar e autorize somente ele aqui.")
            elif not records:
                reason = "MEMORY_DISCLOSURE_NO_ELIGIBLE_MATCH"
                text = "Não encontrei uma lembrança que possa compartilhar por esse pedido. Podemos conferir no privado."
            else:
                record = records[0]
                excerpt = disclosure_excerpt(record, intent)
                if excerpt is None:
                    reason = "MEMORY_DISCLOSURE_NEEDS_REVIEW"
                    text = "Não consegui selecionar um trecho seguro e específico. Vamos conferir no privado."
                else:
                    reason = "MEMORY_DISCLOSURE_AUTHORIZED"
                    text = "Com sua autorização, aqui está o registro que encontrei na memória: " + excerpt
                    # Bind the exact record version to this one outbound/turn.
                    # No scope or remote-consent mutation, no provider payload.
                    grant = {"record_id": str(record.id), "updated_at": record.updated_at.isoformat(),
                             "owner_id": OWNER_ENTITY_ID, "session_id": request.session_id,
                             "turn_id": str(request.turn_id),
                             "expires_at": (self.clock.now() + timedelta(minutes=5)).isoformat()}
        if message.channel_type == ChannelType.GROUP and (
                not public_text_allowed(text) or not audience_text_allowed(text, audience_id=request.session_id)):
            # Do not echo a blocked excerpt or confirm the existence of a record.
            grant = None
            reason = "MEMORY_DISCLOSURE_POLICY_BLOCKED"
            text = "Não posso compartilhar essa informação neste canal."
            if not public_text_allowed(text) or not audience_text_allowed(text, audience_id=request.session_id):
                return InboundResult(decision=decision.model_copy(update={
                    "action": RoutingAction.SILENCE,
                    "reason_codes": [*decision.reason_codes, "CONFIDENTIAL_OUTPUT_SUPPRESSED"],
                }))
        self.cognition.record_turn_decision(
            request, "TAKE_TURN", [reason, "LOCAL_OWNER_DISCLOSURE"], decision.score)
        self.store.add_inbound_reason(
            platform=message.platform, account_scope=message.account_scope,
            conversation_id=message.conversation_id, message_id=message.message_id, reason=reason)
        metadata = {
            "local_control": True, "cognitive_turn_id": str(request.turn_id),
            "generation_stage": "memory_disclosure", "provider": "local_control",
            "generation_error_code": None, "cognitive_reason_codes": [reason],
            "transport_revision": message.transport_revision,
            "delivery_message_id": message.message_id,
        }
        if grant is not None:
            metadata["memory_disclosure"] = grant
        outbound = self._queue_outbound(
            message, decision, text, metadata=metadata,
            reason_codes=[reason, "LOCAL_OWNER_DISCLOSURE"])
        return InboundResult(decision=decision, outbound=[outbound])

    def _explicit_recall_result(self, message, decision, request, intent) -> InboundResult:
        from amora.events import PrivacyScope
        from amora.memory.recall_intent import topic_terms

        shared = message.channel_type != ChannelType.DM
        records = []
        complete = True
        if shared and not self.attention.floor.validate_reply(message, decision):
            return InboundResult(decision=decision.model_copy(update={
                "action": RoutingAction.SILENCE,
                "reason_codes": [*decision.reason_codes, "REPLY_LEASE_INVALIDATED"],
            }))
        if message.privacy_scope not in {PrivacyScope.PERSONAL, PrivacyScope.PRIVATE,
                                         PrivacyScope.PUBLIC, PrivacyScope.SOCIAL}:
            reason = "MEMORY_RECALL_PRIVACY_BLOCKED"
            text = "Não posso consultar lembranças nesse contexto."
        elif not intent.generic and not topic_terms(intent.topic):
            reason = "MEMORY_RECALL_NEEDS_TOPIC"
            text = "Qual informação você quer que eu procure na memória?"
        else:
            # Shared sessions never query personal records, even for their owner.
            # A missing public match does not reveal whether a private one exists.
            records, complete = self.cognition.compiler.memory.store.recall_owner_records(
                OWNER_ENTITY_ID, intent.topic, generic=intent.generic, public_only=shared)
            if shared:
                # No disclosure of private record existence, IDs or content.
                records = [r for r in records if r.privacy_scope == PrivacyScope.PUBLIC]
            if records:
                parts = []
                for record in records:
                    content = record.content
                    if content.startswith("Relato de PERSON_BK: "):
                        content = content[len("Relato de PERSON_BK: "):]
                    content = content.strip()
                    parts.append(content[:500] + ("…" if len(content) > 500 else ""))
                heading = ("Estas são algumas lembranças recentes registradas, não uma transcrição da conversa:"
                           if intent.generic else "Encontrei estes registros na memória:")
                text = heading + "\n" + "\n".join(f"• {part}" for part in dict.fromkeys(parts))
                reason = "MEMORY_RECALL_PUBLIC_FOUND" if shared else "MEMORY_RECALL_PERSISTENT_FOUND"
            elif shared:
                reason = "MEMORY_RECALL_SHARED_SCOPE_RESTRICTED"
                text = ("Neste canal só posso consultar lembranças públicas. "
                        "Para procurar suas memórias pessoais, me chama no privado.")
            elif not complete:
                reason = "MEMORY_RECALL_SEARCH_INCOMPLETE"
                text = "Não encontrei uma correspondência nessa busca limitada. Pode indicar o assunto com mais detalhes?"
            else:
                reason = "MEMORY_RECALL_NO_MATCH"
                text = "Não encontrei uma lembrança correspondente no armazenamento. Isso não significa que você nunca me contou. Pode me dar mais detalhes?"
        if shared and (not public_text_allowed(text) or not audience_text_allowed(text, audience_id=request.session_id)):
            return InboundResult(decision=decision.model_copy(update={
                "action": RoutingAction.SILENCE,
                "reason_codes": [*decision.reason_codes, "CONFIDENTIAL_OUTPUT_SUPPRESSED"],
            }))
        self.cognition.record_turn_decision(
            request, "TAKE_TURN", [reason, "LOCAL_PERSISTENT_MEMORY_RECALL"], decision.score)
        self.store.add_inbound_reason(
            platform=message.platform, account_scope=message.account_scope,
            conversation_id=message.conversation_id, message_id=message.message_id, reason=reason)
        outbound = self._queue_outbound(
            message, decision, text,
            metadata={
                "local_control": True, "cognitive_turn_id": str(request.turn_id),
                "generation_stage": "persistent_recall", "provider": "local_control",
                "generation_error_code": None, "cognitive_reason_codes": [reason],
                "memory_recall_count": len(records), "memory_recall_source": "persistent_store",
                "memory_recall_search_complete": complete,
                # The group receives no private record IDs or status.
                "memory_recall_ids": [str(r.id) for r in records],
                "transport_revision": message.transport_revision,
                "delivery_message_id": message.message_id,
            },
            reason_codes=[reason, "LOCAL_PERSISTENT_MEMORY_RECALL"],
        )
        return InboundResult(decision=decision, outbound=[outbound])

    def _explicit_memory_result(self, message, decision, request, intent) -> InboundResult:
        from amora.events import PrivacyScope
        from amora.memory import MemoryCandidate, MemoryKind, MemoryProvenance, ProvenanceKind

        reason = intent.reason
        memory_id = None
        remote_reuse = False
        if message.channel_type != ChannelType.DM:
            reason = "EXPLICIT_MEMORY_PRIVATE_DM_REQUIRED"
            text = "Me peça para guardar isso em uma conversa privada."
            if not self.attention.floor.validate_reply(message, decision):
                return InboundResult(decision=decision.model_copy(
                    update={"action": RoutingAction.SILENCE,
                            "reason_codes": [*decision.reason_codes, "REPLY_LEASE_INVALIDATED"]}))
        elif not is_verified_owner(self.attention.profiles, message, decision.speaker_entity_id):
            reason = "EXPLICIT_MEMORY_AUTHORITY_REJECTED"
            text = "Não posso registrar uma memória por solicitação direta nessa conversa."
        elif message.privacy_scope not in {PrivacyScope.PERSONAL, PrivacyScope.PRIVATE}:
            reason = "EXPLICIT_MEMORY_PRIVACY_BLOCKED"
            text = "Não posso salvar isso com essa classificação de privacidade."
        elif intent.content is None:
            text = {
                "EXPLICIT_MEMORY_CONTENT_TOO_LONG": "É muita informação para uma lembrança só. Me diga em partes o que devo guardar.",
                "EXPLICIT_MEMORY_CREDENTIAL_BLOCKED": "Não vou guardar senhas ou chaves de acesso na minha memória.",
            }.get(reason, "O que exatamente você quer que eu guarde na memória?")
        else:
            lease = _GENERATION_LEASE.get()
            if lease is None:
                raise InboundGenerationLost("INBOUND_GENERATION_LEASE_LOST")
            remote_reuse = (
                message.privacy_scope == PrivacyScope.PERSONAL
                and message.remote_processing_allowed
                and os.getenv("AMORA_REFLECTION_REMOTE_REUSE", "0") == "1"
            )
            # Keep the source wording and attribution. The request does not
            # grant authority to write a world fact, change identity or run tools.
            candidate = MemoryCandidate(
                kind=MemoryKind.EPISODIC,
                content=f"Relato de {OWNER_ENTITY_ID}: {intent.content}",
                provenance=MemoryProvenance(
                    kind=ProvenanceKind.USER_ASSERTION, source=PROVENANCE_SOURCE,
                    actor_id=OWNER_ENTITY_ID, session_id=request.session_id,
                ),
                privacy_scope=message.privacy_scope, owner_entity_id=OWNER_ENTITY_ID,
                remote_processing_allowed=remote_reuse,
                entity_refs={OWNER_ENTITY_ID: 1.0}, tags={"explicit_owner_memory"},
                importance=1.0,
                metadata={"turn_id": str(request.turn_id), "explicit_owner_request": True},
            )
            try:
                stored = self.cognition.compiler.memory.remember(
                    candidate, record_id=uuid5(NAMESPACE_URL, f"amora:explicit-memory:{request.turn_id}"),
                    explicit_owner_request=True, generation_lease=lease,
                )
            except RuntimeError as exc:
                if str(exc) == "INBOUND_GENERATION_LEASE_LOST":
                    raise InboundGenerationLost("INBOUND_GENERATION_LEASE_LOST") from None
                # No success receipt on a database failure; the inbound retry
                # can safely recover the deterministic record id.
                raise
            if stored.memory is not None:
                memory_id = str(stored.memory.id)
                remote_reuse = stored.memory.remote_processing_allowed
                reason = "EXPLICIT_MEMORY_STORED"
                text = ("Pronto, salvei essa informação na minha memória."
                        if remote_reuse else
                        "Salvei essa informação na minha memória local. Ela ainda não está liberada para eu usar nas conversas com modelos externos.")
            else:
                reason = "EXPLICIT_MEMORY_NOT_STORED"
                text = "Não consegui salvar essa informação na memória. Pode tentar novamente?"

        self.cognition.record_turn_decision(
            request, "TAKE_TURN", [reason, "LOCAL_DETERMINISTIC_CONTROL_REPLY"], decision.score,
        )
        self.store.add_inbound_reason(
            platform=message.platform, account_scope=message.account_scope,
            conversation_id=message.conversation_id, message_id=message.message_id, reason=reason,
        )
        outbound = self._queue_outbound(
            message, decision, text,
            metadata={
                "local_control": True, "cognitive_turn_id": str(request.turn_id),
                "generation_stage": "explicit_memory", "generation_error_code": None,
                "provider": "local_control", "cognitive_reason_codes": [reason],
                "explicit_memory_id": memory_id, "explicit_memory_remote_reuse": remote_reuse,
                "transport_revision": message.transport_revision,
                "delivery_message_id": message.message_id,
            },
            reason_codes=[reason, "LOCAL_DETERMINISTIC_CONTROL_REPLY"],
        )
        return InboundResult(decision=decision, outbound=[outbound])

    def _queue_outbound(
        self,
        message: InboundMessage,
        decision: AttentionDecision,
        text: str,
        *,
        metadata: dict[str, object],
        reason_codes: list[str],
    ) -> OutboundMessage:
        native_source_id = message.native_source_message_id
        outbound = OutboundMessage(
            id=self._outbound_id(message),
            platform=message.platform,
            account_scope=message.account_scope,
            conversation_id=message.conversation_id,
            target_entity_id=decision.speaker_entity_id,
            text=text,
            reply_to_message_id=native_source_id,
            source_message_id=native_source_id,
            created_at=self.clock.now(),
            metadata={
                **metadata,
                "idempotency_key_hash": self._hash(self._inbound_key(message)),
            },
        )
        lease = _GENERATION_LEASE.get()
        if lease is not None and not self.generations.stage(*lease, outbound, decision, reason_codes):
            raise InboundGenerationLost("INBOUND_GENERATION_LEASE_LOST")
        return self._persist_prepared_outbound(message, decision, outbound, reason_codes)

    def _persist_prepared_outbound(self, message, decision, outbound, reason_codes):
        native_source_id = message.native_source_message_id
        metadata = outbound.metadata
        lease = _GENERATION_LEASE.get()
        inserted = (self.store.put_outbound(outbound, generation_lease=lease)
                    if lease is not None else self.store.put_outbound(outbound))
        if not inserted:
            existing = self.store.get_outbound(outbound.id)
            if existing is None:
                raise RuntimeError("deterministic outbound insert conflicted without readable row")
            outbound = existing
        self.store.append_audit(
            MessagingAuditRecord(
                platform=message.platform,
                account_scope=message.account_scope,
                conversation_id=message.conversation_id,
                message_id=str(outbound.id),
                direction=AuditDirection.OUTBOUND,
                channel_type=message.channel_type,
                speaker_entity_id="ENTITY_AMORA",
                text=outbound.text,
                reply_to_message_id=native_source_id,
                routing_action=RoutingAction.RESPOND,
                reason_codes=reason_codes,
                responded=True,
                timestamp=outbound.created_at,
                metadata={
                    "outbound_id": str(outbound.id),
                    "source_message_id_hash": self._hash(native_source_id),
                    "target_entity_id": decision.speaker_entity_id,
                    "transport_revision": message.transport_revision,
                    **{
                        key: value
                        for key, value in metadata.items()
                        if key.startswith("presence_control_") or key == "local_control"
                    },
                },
            )
        )
        self.store.mark_inbound_responded(
            platform=message.platform,
            account_scope=message.account_scope,
            conversation_id=message.conversation_id,
            message_id=message.message_id,
        )
        return outbound

    def invalidate_transport(self, invalidation: TransportInvalidation) -> dict[str, object]:
        generation, floor_invalidated = self.attention.floor.invalidate_source(
            invalidation.platform,
            invalidation.account_scope,
            invalidation.conversation_id,
            invalidation.native_message_id,
        )
        # The worker supplies the new revision on edits; a deletion is final.
        # Unknown revisions fail closed instead of allowing an old input to reappear.
        through = (
            1_000_000
            if "DELETED" in invalidation.reason.upper() or invalidation.transport_revision is None
            else invalidation.transport_revision - 1
        )
        session_id = f"MSG_{invalidation.platform.value.upper()}_{self._hash(invalidation.account_scope + '|' + invalidation.conversation_id)}"
        cancelled_reflections = self.reflections.invalidate_source(
            session_id, self._hash(invalidation.native_message_id), through_revision=through,
        )
        self.generations.invalidate_prepared(session_id, self._hash(invalidation.native_message_id), through)
        cancelled = self.store.cancel_outbound_by_source(
            platform=invalidation.platform, account_scope=invalidation.account_scope,
            conversation_id=invalidation.conversation_id, source_message_id=invalidation.native_message_id,
            reason=invalidation.reason, through_revision=through,
        )
        return {
            "status": "ok",
            "cancelled_reflections": cancelled_reflections,
            "floor_generation": generation,
            "floor_invalidated": floor_invalidated,
            "cancelled_outbound": cancelled,
            "transport_revision": invalidation.transport_revision,
        }

    def state(self) -> dict[str, object]:
        return {
            "messaging_audit_count": self.store.count_audit(),
            "messaging_outbound_pending_count": self.store.pending_outbound_count(),
            "messaging_inflight_count": len(self._inflight),
            "memory_reflection_jobs": self.reflections.counts(),
            "messaging_cognition_timeout_seconds": self.cognition_timeout_seconds,
            "messaging_provider_total_budget_seconds": self.provider_total_budget_seconds,
            "messaging_provider_attempt_budget_seconds": self.provider_attempt_budget_seconds,
        }

    @staticmethod
    def _log_result(
        result: InboundResult,
        *,
        provider: str | None = None,
        fallback_used: bool | None = None,
        cognitive_reason_codes: list[str] | None = None,
        remote_processing_allowed: bool | None = None,
    ) -> None:
        metadata = result.outbound[0].metadata if result.outbound else {}
        _LOG.info(
            "messaging_inbound_result action=%s outbound=%d duplicate=%s provider=%s fallback_used=%s remote_processing_allowed=%s attention_reasons=%s cognitive_reasons=%s",
            result.decision.action.value,
            len(result.outbound),
            result.duplicate,
            provider or "none",
            fallback_used,
            remote_processing_allowed,
            ",".join(result.decision.reason_codes) or "none",
            ",".join(cognitive_reason_codes or []) or "none",
            extra={
                "turn_id": metadata.get("cognitive_turn_id"),
                "model": metadata.get("model"),
                "generation_stage": metadata.get("generation_stage"),
                "generation_error_code": metadata.get("generation_error_code"),
                "generation_latency_ms": metadata.get("generation_latency_ms"),
            },
        )

    @classmethod
    def _turn_id(cls, message: InboundMessage) -> UUID:
        return uuid5(NAMESPACE_URL, f"amora:messaging-turn:{cls._inbound_key(message)}")

    @classmethod
    def _outbound_id(cls, message: InboundMessage) -> UUID:
        return uuid5(NAMESPACE_URL, f"amora:messaging-outbound:{cls._inbound_key(message)}")

    @staticmethod
    def _inbound_key(message: InboundMessage) -> str:
        return "|".join(
            (
                message.platform.value,
                message.account_scope,
                message.conversation_id,
                message.message_id,
            )
        )

    @staticmethod
    def _cognitive_text(message: InboundMessage) -> str:
        if message.text.strip():
            return message.text.strip()
        labels = []
        for attachment in message.attachments[:4]:
            label = attachment.media_type or "arquivo"
            if attachment.filename:
                label += f" ({attachment.filename})"
            labels.append(label)
        joined = ", ".join(labels) if labels else "anexo"
        return f"[Mensagem contém anexo não analisado: {joined}]"

    @staticmethod
    def _hash(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:20]

    @classmethod
    def _conversation_ref(cls, message: InboundMessage) -> str:
        return f"{message.platform.value}:{cls._hash(message.conversation_id)}"

    @classmethod
    def _session_ref(cls, message: InboundMessage) -> str:
        return f"MSG_{message.platform.value.upper()}_{cls._hash(message.account_scope + '|' + message.conversation_id)}"
