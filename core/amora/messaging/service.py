from __future__ import annotations

import hashlib

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


class PlatformMessagingService:
    """Common messaging pipeline between platform workers and Cognitive Core.

    Platform workers normalize transport facts. Core owns identity resolution,
    DM authorization, group attention, cognition, presence-control authority and
    outbound intent. No adapter is allowed to decide canonical speaker identity,
    family truth or whether a person may control Amora's transport presence.
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
    ) -> None:
        self.store = store
        self.attention = attention
        self.cognition = cognition
        self.conversation = conversation
        self.clock = clock
        self.presence_control = presence_control or PlatformPresenceControlService(
            profiles=attention.profiles,
            presence=PlatformPresenceController(store, clock, reset_transient=False),
        )

    async def receive(self, message: InboundMessage) -> InboundResult:
        if self.store.has_audit(
            platform=message.platform,
            account_scope=message.account_scope,
            conversation_id=message.conversation_id,
            message_id=message.message_id,
        ):
            return InboundResult(
                decision=AttentionDecision(
                    action=RoutingAction.SILENCE,
                    score=0.0,
                    speaker_actor_ref="DUPLICATE",
                    reason_codes=["DUPLICATE_PLATFORM_MESSAGE", "NO_REPROCESS"],
                ),
                duplicate=True,
            )

        decision = self.attention.route(message)
        actor_id = decision.speaker_entity_id or decision.speaker_actor_ref
        cognitive_text = self._cognitive_text(message)
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
            },
        )
        if not self.store.append_audit(inbound_audit):
            return InboundResult(
                decision=decision.model_copy(
                    update={"reason_codes": [*decision.reason_codes, "AUDIT_DEDUPE_RACE"]}
                ),
                duplicate=True,
            )

        if decision.action in {RoutingAction.BLOCK, RoutingAction.QUARANTINE, RoutingAction.SILENCE}:
            return InboundResult(decision=decision)

        if decision.action == RoutingAction.RESPOND:
            control = self.presence_control.handle(
                message.text,
                speaker_entity_id=decision.speaker_entity_id,
                account_scope=message.account_scope,
            )
            if control is not None:
                return self._presence_control_result(message, decision, control)

        request = CognitiveRequest(
            text=cognitive_text,
            speaker_id=actor_id,
            target_id=("ENTITY_AMORA" if decision.action == RoutingAction.RESPOND else None),
            source_event_id=None,
            session_id=self._session_ref(message),
            privacy_scope=message.privacy_scope,
            remote_processing_allowed=message.remote_processing_allowed,
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
            return InboundResult(decision=decision)

        if decision.action != RoutingAction.RESPOND:
            return InboundResult(decision=decision)

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
                source_event_id=None,
                privacy_scope=message.privacy_scope,
                metadata=request.metadata,
            )

        fallback = self.conversation.composer.fallback_dialogue(cognitive_text, speaker_id=actor_id)
        plan = await self.cognition.respond(request, on_segment=None, fallback_text=fallback)
        await self.cognition.emit_event(plan)

        if message.channel_type == ChannelType.GROUP and not self.attention.floor.validate_reply(message, decision):
            self.store.add_inbound_reason(
                platform=message.platform,
                account_scope=message.account_scope,
                conversation_id=message.conversation_id,
                message_id=message.message_id,
                reason="REPLY_LEASE_INVALIDATED",
            )
            return InboundResult(
                decision=decision.model_copy(
                    update={
                        "action": RoutingAction.SILENCE,
                        "reason_codes": [*decision.reason_codes, "REPLY_LEASE_INVALIDATED"],
                    }
                )
            )

        text = (plan.display_text or plan.speech_text or fallback).strip()
        if not text:
            self.store.add_inbound_reason(
                platform=message.platform,
                account_scope=message.account_scope,
                conversation_id=message.conversation_id,
                message_id=message.message_id,
                reason="EMPTY_REALIZATION_SUPPRESSED",
            )
            return InboundResult(decision=decision)

        outbound = self._queue_outbound(
            message,
            decision,
            text,
            metadata={
                "cognitive_turn_id": str(plan.turn_id),
                "provider": plan.provider,
                "model": plan.model,
                "floor_generation": decision.floor_generation,
                "attention_score": decision.score,
                "transport_revision": message.transport_revision,
                "delivery_message_id": message.message_id,
            },
            reason_codes=["CORE_OUTBOUND_ENQUEUED", *plan.reason_codes],
        )
        return InboundResult(decision=decision, outbound=[outbound])

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
            platform=message.platform,
            account_scope=message.account_scope,
            conversation_id=message.conversation_id,
            target_entity_id=decision.speaker_entity_id,
            text=text,
            reply_to_message_id=native_source_id,
            source_message_id=native_source_id,
            created_at=self.clock.now(),
            metadata=metadata,
        )
        self.store.put_outbound(outbound)
        self.store.append_audit(
            MessagingAuditRecord(
                platform=message.platform,
                account_scope=message.account_scope,
                conversation_id=message.conversation_id,
                message_id=str(outbound.id),
                direction=AuditDirection.OUTBOUND,
                channel_type=message.channel_type,
                speaker_entity_id="ENTITY_AMORA",
                text=text,
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
        }

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
