from __future__ import annotations

import asyncio
import hashlib
import logging
from uuid import NAMESPACE_URL, UUID, uuid5

from amora.cognition import CognitiveRequest

from .attention import SocialAttentionRouter
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
from .presence import PlatformPresenceController
from .presence_control import PlatformPresenceControlService, PresenceControlOutcome
from .store import MessagingStore


_LOG = logging.getLogger("amora.messaging")
_MESSAGING_PROVIDER_TOTAL_BUDGET_SECONDS = 20.0
_MESSAGING_PROVIDER_ATTEMPT_BUDGET_SECONDS = 12.0
_MESSAGING_COGNITION_TIMEOUT_SECONDS = 24.0
_TERMINAL_DUPLICATE_REASONS = frozenset(
    {
        "REPLY_LEASE_INVALIDATED",
        "EMPTY_REALIZATION_SUPPRESSED",
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
                self._receive_once(message),
                name=f"amora-messaging-{self._hash(key)}",
            )
            self._inflight[key] = task
        try:
            return await asyncio.shield(task)
        finally:
            if task.done() and self._inflight.get(key) is task:
                self._inflight.pop(key, None)

    async def _receive_once(self, message: InboundMessage) -> InboundResult:
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
            decision = self.attention.route(message).model_copy(
                update={
                    "reason_codes": [
                        *self.attention.route(message).reason_codes,
                        "DUPLICATE_RECOVERY_RESUME",
                    ]
                }
            )
            # The previous request wrote the inbound audit but never reached a
            # terminal decision/outbound. Resume the same deterministic turn.
            duplicate_recovery = True
        else:
            decision = self.attention.route(message)
            duplicate_recovery = False
            actor_id = decision.speaker_entity_id or decision.speaker_actor_ref
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

        if decision.action == RoutingAction.RESPOND:
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
                "memory_consolidation": False,
                "relationship_learning": False,
                "messaging_audit": True,
                "provider_total_budget_seconds": self.provider_total_budget_seconds,
                "provider_attempt_budget_seconds": self.provider_attempt_budget_seconds,
            },
        )
        self.cognition.observe_input(request)

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

        fallback = self.conversation.composer.fallback_dialogue(cognitive_text, speaker_id=actor_id)
        provider: str | None = None
        model: str | None = None
        fallback_used = False
        cognitive_reasons: list[str] = []
        cognitive_turn_id = str(request.turn_id)
        try:
            plan = await asyncio.wait_for(
                self.cognition.respond(request, on_segment=None, fallback_text=fallback),
                timeout=self.cognition_timeout_seconds,
            )
            await self.cognition.emit_event(plan)
            provider = plan.provider
            model = plan.model
            fallback_used = bool(plan.fallback_used)
            cognitive_reasons = list(plan.reason_codes)
            cognitive_turn_id = str(plan.turn_id)
            text = (plan.display_text or plan.speech_text or fallback).strip()
        except TimeoutError:
            fallback_used = True
            cognitive_reasons = ["MESSAGING_COGNITION_HARD_TIMEOUT", "LOCAL_FALLBACK"]
            text = fallback.strip() or "Uhum."
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

        if not text:
            self.store.add_inbound_reason(
                platform=message.platform,
                account_scope=message.account_scope,
                conversation_id=message.conversation_id,
                message_id=message.message_id,
                reason="EMPTY_REALIZATION_SUPPRESSED",
            )
            result = InboundResult(
                decision=decision.model_copy(
                    update={
                        "reason_codes": [*decision.reason_codes, "EMPTY_REALIZATION_SUPPRESSED"],
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
            return InboundResult(decision=decision, duplicate=True, outbound=[outbound])

        terminal_reason = next((reason for reason in reasons if reason in _TERMINAL_DUPLICATE_REASONS), None)
        action = audit.routing_action
        if terminal_reason is not None:
            action = RoutingAction.SILENCE
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
        if terminal_reason is not None:
            return InboundResult(
                decision=AttentionDecision(
                    action=RoutingAction.SILENCE,
                    score=0.0,
                    speaker_entity_id=audit.speaker_entity_id,
                    speaker_actor_ref=actor_ref,
                    reason_codes=[*reasons, terminal_reason, "DUPLICATE_TERMINAL_REUSED"],
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
        inserted = self.store.put_outbound(outbound)
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
        generation = self.attention.floor.invalidate(
            invalidation.platform,
            invalidation.account_scope,
            invalidation.conversation_id,
        )
        cancelled = self.store.cancel_outbound_by_source(
            platform=invalidation.platform,
            account_scope=invalidation.account_scope,
            conversation_id=invalidation.conversation_id,
            source_message_id=invalidation.native_message_id,
            reason=invalidation.reason,
        )
        return {
            "status": "ok",
            "floor_generation": generation,
            "cancelled_outbound": cancelled,
            "transport_revision": invalidation.transport_revision,
        }

    def state(self) -> dict[str, object]:
        return {
            "messaging_audit_count": self.store.count_audit(),
            "messaging_outbound_pending_count": self.store.pending_outbound_count(),
            "messaging_inflight_count": len(self._inflight),
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
