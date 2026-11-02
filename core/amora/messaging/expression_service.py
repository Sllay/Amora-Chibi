from __future__ import annotations

import unicodedata

from .models import ExpressionKind, ExpressionRef, InboundMessage, MessagingPlatform
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
    ):
        expression = self._requested_sticker_reuse(message)
        if expression is None:
            return super()._queue_outbound(
                message,
                decision,
                text,
                metadata=metadata,
                reason_codes=reason_codes,
            )

        expression_dump = expression.model_dump(mode="json")
        return super()._queue_outbound(
            message,
            decision,
            "",
            metadata={
                **metadata,
                "transport_expressions": [expression_dump],
                "expression_only": True,
                "expression_source_message_id": message.native_source_message_id,
            },
            reason_codes=[*reason_codes, "DISCORD_STICKER_REUSE_REQUESTED"],
        )

    @classmethod
    def _requested_sticker_reuse(cls, message: InboundMessage) -> ExpressionRef | None:
        if message.platform != MessagingPlatform.DISCORD or not message.expressions:
            return None
        text = cls._normalized_expression_request(message.text)
        if not text:
            return None

        negative = (
            "nao manda",
            "nao envia",
            "nao use",
            "nao usa",
            "não manda",
            "não envia",
            "não use",
            "não usa",
        )
        if any(token in message.text.lower() for token in negative):
            return None

        verbs = ("manda", "envia", "usa", "use", "solta", "joga")
        references = ("essa", "esta", "figurinha", "fig", "sticker", "adesivo", "de volta")
        if not any(token in text.split() for token in verbs):
            return None
        if not any(token in text for token in references):
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
