from __future__ import annotations

import hashlib
import re
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


_REQUEST_VERBS = {
    "manda", "mandar", "envia", "enviar", "usa", "usar", "use",
    "solta", "soltar", "joga", "jogar",
}
_REQUEST_REFERENCES = {
    "essa", "esta", "uma", "figurinha", "figurinhas", "fig", "sticker",
    "stickers", "adesivo", "adesivos",
}
_STOP_WORDS = {
    "a", "ai", "amora", "aqui", "as", "da", "das", "de", "do", "dos",
    "essa", "esta", "eu", "fig", "figurinha", "figurinhas", "grupo", "no",
    "o", "os", "pra", "pro", "que", "sticker", "stickers", "tipo", "uma",
    "vc", "voce",
} | _REQUEST_VERBS
_CUE_GROUPS = (
    ({"kkk", "kkkk", "haha", "rir", "rindo", "engracado", "morre", "morrendo", "lol", "😂", "🤣", "💀"},
     {"laugh", "laughing", "lol", "funny", "morre", "morrendo", "rindo", "rir", "😂", "🤣", "💀"}),
    ({"raiva", "bravo", "brava", "odio", "puto", "puta", "nervoso", "nervosa", "😡", "🤬"},
     {"angry", "mad", "rage", "raiva", "bravo", "brava", "odio", "😡", "🤬"}),
    ({"triste", "tristeza", "chorando", "choro", "sad", "😭", "😢"},
     {"sad", "cry", "crying", "triste", "tristeza", "chorando", "😭", "😢"}),
    ({"amor", "amo", "fofo", "fofa", "carinho", "love", "❤", "❤️", "🥰"},
     {"love", "heart", "amor", "carinho", "fofo", "fofa", "❤", "❤️", "🥰"}),
    ({"surpreso", "surpresa", "chocado", "choque", "wtf", "😱", "😮"},
     {"surprise", "surprised", "shock", "shocked", "wtf", "surpresa", "😱", "😮"}),
)


class PlatformMessagingService(BasePlatformMessagingService):
    """Project transport-native social expressions without trusting model IDs.

    Discord supplies the real sticker catalogue. The catalogue stays local to this
    service and is never added to CognitiveRequest metadata, therefore providers do
    not receive native sticker ids. A provider may produce ordinary validated text;
    the Core then optionally replaces that text with a locally selected sticker when
    the user explicitly asked for one.
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
        selection = self._requested_sticker_expression(message, text)
        if selection is None:
            return super()._queue_outbound(
                message,
                decision,
                text,
                metadata=metadata,
                reason_codes=reason_codes,
            )

        expression, source = selection
        native_source_id = message.native_source_message_id
        expression_dump = expression.model_dump(mode="json")
        expression_metadata = {
            **metadata,
            "transport_expressions": [expression_dump],
            "expression_only": True,
            "expression_selection_source": source,
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

        reason = "DISCORD_STICKER_SOURCE_REUSED" if source == "source_message" else "DISCORD_STICKER_CATALOG_SELECTED"
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
                reason_codes=[*reason_codes, reason],
                responded=True,
                timestamp=outbound.created_at,
                metadata={
                    "outbound_id": str(outbound.id),
                    "source_message_id_hash": self._hash(native_source_id),
                    "target_entity_id": decision.speaker_entity_id,
                    "transport_revision": message.transport_revision,
                    "expression_kind": expression.kind.value,
                    "expression_name": expression.name,
                    "expression_selection_source": source,
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
        """Compatibility helper retained for focused tests/callers."""
        selection = cls._requested_sticker_expression(message, "")
        return selection[0] if selection is not None else None

    @classmethod
    def _requested_sticker_expression(
        cls,
        message: InboundMessage,
        response_text: str,
    ) -> tuple[ExpressionRef, str] | None:
        if message.platform != MessagingPlatform.DISCORD:
            return None
        text = cls._normalized_expression_request(message.text)
        if not cls._is_explicit_sticker_request(text):
            return None

        source = cls._first_usable_sticker(message.expressions, message.guild_or_group_id)
        if source is not None:
            return source, "source_message"

        catalog = [
            item
            for item in message.available_expressions
            if item.kind == ExpressionKind.STICKER
            and (not message.guild_or_group_id or not item.guild_or_group_id or item.guild_or_group_id == message.guild_or_group_id)
        ]
        if not catalog:
            return None
        return cls._select_catalog_sticker(message, response_text, catalog), "guild_catalog"

    @classmethod
    def _is_explicit_sticker_request(cls, text: str) -> bool:
        if not text:
            return False
        if any(token in text for token in ("nao manda", "nao enviar", "nao envia", "nao use", "nao usar", "nao usa")):
            return False
        words = set(cls._tokens(text))
        if not words.intersection(_REQUEST_VERBS):
            return False
        return bool(words.intersection(_REQUEST_REFERENCES) or "de volta" in text)

    @staticmethod
    def _first_usable_sticker(expressions: list[ExpressionRef], guild_id: str | None) -> ExpressionRef | None:
        for expression in expressions:
            if expression.kind != ExpressionKind.STICKER:
                continue
            if guild_id and expression.guild_or_group_id and expression.guild_or_group_id != guild_id:
                continue
            return expression
        return None

    @classmethod
    def _select_catalog_sticker(
        cls,
        message: InboundMessage,
        response_text: str,
        catalog: list[ExpressionRef],
    ) -> ExpressionRef:
        query_text = f"{message.text} {response_text}".strip()
        query_normalized = cls._normalized_expression_request(query_text)
        query_tokens = {token for token in cls._tokens(query_normalized) if token not in _STOP_WORDS}

        scored: list[tuple[int, int, ExpressionRef]] = []
        for index, expression in enumerate(catalog):
            descriptor_raw = " ".join(
                part for part in [expression.name or "", expression.description or "", " ".join(expression.tags)] if part
            )
            descriptor_normalized = cls._normalized_expression_request(descriptor_raw)
            descriptor_tokens = set(cls._tokens(descriptor_normalized))
            score = len(query_tokens.intersection(descriptor_tokens)) * 4
            if expression.name and cls._normalized_expression_request(expression.name) in query_normalized:
                score += 5
            for query_cues, sticker_cues in _CUE_GROUPS:
                if any(cue in query_text.lower() or cue in query_tokens for cue in query_cues):
                    if any(cue in descriptor_raw.lower() or cue in descriptor_tokens for cue in sticker_cues):
                        score += 7
            scored.append((score, -index, expression))

        best_score = max(item[0] for item in scored)
        if best_score > 0:
            return max(scored, key=lambda item: (item[0], item[1]))[2]

        # Generic explicit request: choose deterministically from the trusted
        # catalogue so retries/redeploys cannot change the selected expression.
        digest = hashlib.sha256(message.message_id.encode("utf-8")).digest()
        index = int.from_bytes(digest[:4], "big") % len(catalog)
        return catalog[index]

    @staticmethod
    def _tokens(value: str) -> list[str]:
        return re.findall(r"[a-z0-9_]+", value.lower())

    @staticmethod
    def _normalized_expression_request(value: str) -> str:
        decomposed = unicodedata.normalize("NFKD", value.lower())
        ascii_value = "".join(char for char in decomposed if not unicodedata.combining(char))
        return " ".join(ascii_value.split())
