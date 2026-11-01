from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from amora.identity import PersonProfileService
from amora.time import Clock

from .models import AttentionDecision, ChannelType, InboundMessage, MessagingPlatform, RoutingAction


_GROUP_FLOOR_SECONDS = 35
_REPLY_LEASE_SECONDS = 10
_AMORA_VOCATIVE = re.compile(
    r"(?:^|[\s(\[])@?amora\b(?:\s*[,!?:;\-–—]|\s+)",
    re.IGNORECASE,
)


@dataclass(slots=True)
class _Floor:
    generation: int
    speaker_actor_ref: str
    speaker_entity_id: str | None
    opened_at: datetime
    expires_at: datetime
    anchor_message_id: str
    reason: str


class ConversationFloor:
    """Short group-conversation lease owned by Core, never by a platform adapter."""

    def __init__(self, clock: Clock) -> None:
        self.clock = clock
        self._floors: dict[tuple[str, str, str], _Floor] = {}
        self._generation: dict[tuple[str, str, str], int] = {}

    @staticmethod
    def key(message: InboundMessage) -> tuple[str, str, str]:
        return (message.platform.value, message.account_scope, message.conversation_id)

    @staticmethod
    def raw_key(
        platform: MessagingPlatform | str,
        account_scope: str,
        conversation_id: str,
    ) -> tuple[str, str, str]:
        platform_value = platform.value if isinstance(platform, MessagingPlatform) else str(platform)
        return (platform_value, account_scope, conversation_id)

    def generation(self, message: InboundMessage) -> int:
        return self._generation.get(self.key(message), 0)

    def active(self, message: InboundMessage) -> _Floor | None:
        key = self.key(message)
        floor = self._floors.get(key)
        if floor is None:
            return None
        if self.clock.now() > floor.expires_at:
            self._floors.pop(key, None)
            return None
        return floor

    def open(
        self,
        message: InboundMessage,
        *,
        actor_ref: str,
        speaker_entity_id: str | None,
        reason: str,
    ) -> _Floor:
        key = self.key(message)
        generation = self._generation.get(key, 0) + 1
        self._generation[key] = generation
        now = self.clock.now()
        floor = _Floor(
            generation=generation,
            speaker_actor_ref=actor_ref,
            speaker_entity_id=speaker_entity_id,
            opened_at=now,
            expires_at=now + timedelta(seconds=_GROUP_FLOOR_SECONDS),
            anchor_message_id=message.native_source_message_id,
            reason=reason,
        )
        self._floors[key] = floor
        return floor

    def shift_away(self, message: InboundMessage) -> int:
        return self.invalidate(
            message.platform,
            message.account_scope,
            message.conversation_id,
        )

    def invalidate(
        self,
        platform: MessagingPlatform | str,
        account_scope: str,
        conversation_id: str,
    ) -> int:
        key = self.raw_key(platform, account_scope, conversation_id)
        generation = self._generation.get(key, 0) + 1
        self._generation[key] = generation
        self._floors.pop(key, None)
        return generation

    def reply_lease(self, floor: _Floor) -> datetime:
        return min(
            floor.expires_at,
            self.clock.now() + timedelta(seconds=_REPLY_LEASE_SECONDS),
        )

    def validate_reply(
        self,
        message: InboundMessage,
        decision: AttentionDecision,
    ) -> bool:
        if message.channel_type == ChannelType.DM:
            return decision.action == RoutingAction.RESPOND
        if decision.reply_lease_until is not None and self.clock.now() > decision.reply_lease_until:
            return False
        floor = self.active(message)
        if floor is None:
            return False
        return floor.generation == decision.floor_generation


class SocialAttentionRouter:
    """Deterministic social gate before Cognitive Core.

    DM authorization is identity based. Group response authority is evidence based:
    native reply and mention outrank text heuristics; uncertainty becomes silence.
    """

    def __init__(self, *, profiles: PersonProfileService, clock: Clock) -> None:
        self.profiles = profiles
        self.clock = clock
        self.floor = ConversationFloor(clock)

    def resolve_speaker(self, message: InboundMessage) -> tuple[str | None, str]:
        refs = sorted(message.sender_identifiers, key=lambda item: item.confidence, reverse=True)
        for ref in refs:
            entity = self.profiles.resolve_actor(
                platform=message.platform.value,
                identifier_type=ref.identifier_type,
                external_actor_id=ref.external_actor_id,
                account_scope=message.account_scope,
            )
            if entity is not None:
                return entity.id, self._actor_ref(message)
        return None, self._actor_ref(message)

    def route(self, message: InboundMessage) -> AttentionDecision:
        speaker_entity_id, actor_ref = self.resolve_speaker(message)

        if message.channel_type == ChannelType.DM:
            if speaker_entity_id is None:
                return AttentionDecision(
                    action=RoutingAction.QUARANTINE,
                    score=0.0,
                    speaker_actor_ref=actor_ref,
                    reason_codes=["DM_UNKNOWN_BLOCKED_BEFORE_COGNITION"],
                )
            return AttentionDecision(
                action=RoutingAction.RESPOND,
                score=1.0,
                speaker_entity_id=speaker_entity_id,
                speaker_actor_ref=actor_ref,
                addressee_id="ENTITY_AMORA",
                reason_codes=["DM_KNOWN_PERSON", "IDENTITY_BINDING_RESOLVED"],
            )

        if message.mentioned_actor_ids and not message.mentions_amora:
            generation = self.floor.shift_away(message)
            return AttentionDecision(
                action=RoutingAction.CONTEXT_ONLY,
                score=0.08,
                speaker_entity_id=speaker_entity_id,
                speaker_actor_ref=actor_ref,
                reason_codes=["GROUP_EXPLICIT_OTHER_MENTION", "FLOOR_SHIFTED_AWAY"],
                floor_generation=generation,
            )

        direct_reason: str | None = None
        score = 0.0
        if message.reply_to_amora:
            direct_reason = "GROUP_NATIVE_REPLY_TO_AMORA"
            score = 1.0
        elif message.mentions_amora:
            direct_reason = "GROUP_NATIVE_MENTION_AMORA"
            score = 0.995
        elif _AMORA_VOCATIVE.search(message.text):
            direct_reason = "GROUP_EXPLICIT_VOCATIVE_AMORA"
            score = 0.98

        if direct_reason is not None:
            floor = self.floor.open(
                message,
                actor_ref=actor_ref,
                speaker_entity_id=speaker_entity_id,
                reason=direct_reason,
            )
            return AttentionDecision(
                action=RoutingAction.RESPOND,
                score=score,
                speaker_entity_id=speaker_entity_id,
                speaker_actor_ref=actor_ref,
                addressee_id="ENTITY_AMORA",
                reason_codes=[direct_reason, "GROUP_DIRECT_ATTENTION"],
                floor_generation=floor.generation,
                reply_lease_until=self.floor.reply_lease(floor),
            )

        active = self.floor.active(message)
        if active is not None and active.speaker_actor_ref == actor_ref:
            return AttentionDecision(
                action=RoutingAction.RESPOND,
                score=0.82,
                speaker_entity_id=speaker_entity_id,
                speaker_actor_ref=actor_ref,
                addressee_id="ENTITY_AMORA",
                reason_codes=["GROUP_ACTIVE_FLOOR_SAME_SPEAKER", "BOUNDED_CONTINUITY"],
                floor_generation=active.generation,
                reply_lease_until=self.floor.reply_lease(active),
            )

        if active is not None and active.speaker_actor_ref != actor_ref:
            generation = self.floor.shift_away(message)
            return AttentionDecision(
                action=RoutingAction.CONTEXT_ONLY,
                score=0.22,
                speaker_entity_id=speaker_entity_id,
                speaker_actor_ref=actor_ref,
                reason_codes=["GROUP_OTHER_SPEAKER_TOOK_FLOOR", "FLOOR_SHIFTED_AWAY"],
                floor_generation=generation,
            )

        return AttentionDecision(
            action=RoutingAction.CONTEXT_ONLY,
            score=0.15,
            speaker_entity_id=speaker_entity_id,
            speaker_actor_ref=actor_ref,
            reason_codes=["GROUP_AMBIENT_CONTEXT", "NO_DIRECT_ATTENTION"],
            floor_generation=self.floor.generation(message),
        )

    @staticmethod
    def _actor_ref(message: InboundMessage) -> str:
        stable = "|".join(
            sorted(
                f"{item.identifier_type}:{item.external_actor_id}"
                for item in message.sender_identifiers
            )
        )
        digest = hashlib.sha256(
            f"{message.platform.value}|{message.account_scope}|{stable}".encode("utf-8")
        ).hexdigest()[:20]
        return f"EXT_{message.platform.value.upper()}_{digest}"
