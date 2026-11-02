from __future__ import annotations

import unicodedata

from .models import (
    AuditDirection,
    ExpressionKind,
    ExpressionRef,
    InboundMessage,
    MessagingAuditRecord,
    MessagingPlatform,
    OutboundMessage,
    RoutingAction,
)
from .service import PlatformMessagingService as BasePlatformMessagingService


class PlatformMessagingService(BasePlatformMessagingService):
    """Messaging service with transport-native social expression projection.

    The cognitive layer still decides whether Amora is allowed to answer. This
    layer only projects an explicit, source-scoped request such as "manda essa de
    volta" into a native Discord sticker attached to the already-authorized
    outbound. No arbitrary sticker ID supplied in text is trusted.
    """

    def _queue_outbound(
        self,
        message: InboundMessage,
        decision,
        text: str,
        *,
        metadata: dict[str, object],
        reason_codes: list[str],
    ) -> OutboundMessage:
        expression = self._requested_sticker_reuse(message)
        if expression is None:
            return super()._queue_outbound(
                message,
                decision,
                text,
                metadata=metadata,
                reason_codes=reason_codes,
            )

        native_source_id = message.native_source_message_id
        expression_dump = expression.model_dump(mode="json")
        expression_metadata = {
            **metadata,
            "transport_expressions": [expression_dump],
            "expression_only": True,
            "expression_source_message_id": native_source_id,
            "idempotency_key_hash": self._hash(self._inbound_key(message)),
        }
        outbound = OutboundMessage(
            id=self._outbound_id(message),
            platform=message.platform,
            account_scope=message.account_scope,
            conversation_id=message.conversation_id,
            target_entity_id=decision.speaker_entity_id,
            text="",
            expressions=[expression],
            reply_to_message_id=native_source_id,
            source_message_id=native_source_id,
            created_at=self.clock.now(),
            metadata=expression_metadata,
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
                text="",
                reply_to_message_id=native_source_id,
                routing_action=RoutingAction.RESPOND,
                reason_codes=[*reason_codes, "DISCORD_STICKER_REUSE_REQUESTED"],
                responded=True,
                timestamp=outbound.created_at,
                metadata={
                    "outbound_id": str(outbound.id),
                    "source_message_id_hash": self._hash(native_source_id),
                    "target_entity_id": decision.speaker_entity_id,
                    "transport_revision": message.transport_revision,
                    "expression_kind": expression.kind.value,
                    "expression_name": expression.name,
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

    @classmethod
    def _requested_sticker_reuse(cls, message: InboundMessage) -> ExpressionRef | None:
        if message.platform != MessagingPlatform.DISCORD or not message.expressions:
            return None
        text = cls._normalized_expression_request(message.text)
        if not text:
            return None

        negative = (
            "nao manda",
            "nao enviar",
            "nao envia",
            "nao use",
            "nao usar",
            "nao usa",
        )
        if any(token in text for token in negative):
            return None

        words = set(text.split())
        verbs = {
            "manda",
            "mandar",
            "envia",
            "enviar",
            "usa",
            "usar",
            "use",
            "solta",
            "soltar",
            "joga",
            "jogar",
        }
        references = {
            "essa",
            "esta",
            "figurinha",
            "figurinhas",
            "fig",
            "sticker",
            "stickers",
            "adesivo",
            "adesivos",
        }
        if not words.intersection(verbs):
            return None
        if not words.intersection(references) and "de volta" not in text:
            return None

        for expression in message.expressions:
            if expression.kind != ExpressionKind.STICKER:
                continue
            if (
                message.guild_or_group_id
                and expression.guild_or_group_id
                and expression.guild_or_group_id != message.guild_or_group_id
            ):
                continue
            return expression
        return None

    @staticmethod
    def _normalized_expression_request(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.lower())
        ascii_value = "".join(char for char in decomposed if not unicodedata.combining(char))
        return " ".join(ascii_value.split())
