from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime, timedelta

from amora.identity import PersonProfileService
from amora.time import Clock

from .models import AttentionDecision, ChannelType, InboundMessage, MessagingPlatform, RoutingAction


_GROUP_FLOOR_SECONDS = 35
_CONTINUITY_REPLY_LEASE_SECONDS = 10
_DIRECT_REPLY_LEASE_SECONDS = 90
_DIRECT_ATTENTION_REASONS = frozenset(
    {
        "GROUP_NATIVE_REPLY_TO_AMORA",
        "GROUP_NATIVE_MENTION_AMORA",
        "GROUP_EXPLICIT_VOCATIVE_AMORA",
    }
)
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
    """Short Core-owned group-conversation lease.

    When a canonical database is available, floor authority is stored there so a
    follow-up can land on another Core process/container and still inherit the
    same bounded conversation. The in-memory path remains for isolated unit tests
    and embedded runtimes without canonical persistence.
    """

    def __init__(self, clock: Clock, database=None) -> None:
        self.clock = clock
        self.database = database if hasattr(database, "_conn") and hasattr(database, "_lock") else None
        self._floors: dict[tuple[str, str, str], _Floor] = {}
        self._generation: dict[tuple[str, str, str], int] = {}
        self._response_sources: dict[tuple[str, str, str], set[str]] = {}

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

    @staticmethod
    def _row_to_floor(row) -> _Floor | None:
        if row is None or not row["speaker_actor_ref"] or not row["expires_at"]:
            return None
        return _Floor(
            generation=int(row["generation"]),
            speaker_actor_ref=str(row["speaker_actor_ref"]),
            speaker_entity_id=str(row["speaker_entity_id"]) if row["speaker_entity_id"] else None,
            opened_at=datetime.fromisoformat(row["opened_at"]),
            expires_at=datetime.fromisoformat(row["expires_at"]),
            anchor_message_id=str(row["anchor_message_id"] or ""),
            reason=str(row["reason"] or ""),
        )

    def _db_row(self, key: tuple[str, str, str]):
        assert self.database is not None
        return self.database._conn.execute(
            """SELECT * FROM messaging_conversation_floor
               WHERE platform=? AND account_scope=? AND conversation_id=?""",
            key,
        ).fetchone()

    def generation(self, message: InboundMessage) -> int:
        key = self.key(message)
        if self.database is None:
            return self._generation.get(key, 0)
        with self.database._lock:
            row = self._db_row(key)
        return int(row["generation"]) if row is not None else 0

    def active(self, message: InboundMessage) -> _Floor | None:
        key = self.key(message)
        if self.database is None:
            floor = self._floors.get(key)
            if floor is None:
                return None
            if self.clock.now() > floor.expires_at:
                self._floors.pop(key, None)
                return None
            return floor

        with self.database._lock, self.database._conn:
            row = self._db_row(key)
            floor = self._row_to_floor(row)
            if floor is None:
                return None
            if self.clock.now() > floor.expires_at:
                self.database._conn.execute(
                    """UPDATE messaging_conversation_floor
                       SET speaker_actor_ref=NULL, speaker_entity_id=NULL, opened_at=NULL,
                           expires_at=NULL, anchor_message_id=NULL,
                           response_source_message_id=NULL, reason=NULL
                       WHERE platform=? AND account_scope=? AND conversation_id=?""",
                    key,
                )
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
        now = self.clock.now()
        expires_at = now + timedelta(seconds=_GROUP_FLOOR_SECONDS)
        if self.database is None:
            generation = self._generation.get(key, 0) + 1
            self._generation[key] = generation
            floor = _Floor(
                generation=generation,
                speaker_actor_ref=actor_ref,
                speaker_entity_id=speaker_entity_id,
                opened_at=now,
                expires_at=expires_at,
                anchor_message_id=message.native_source_message_id,
                reason=reason,
            )
            self._floors[key] = floor
            self._response_sources[key] = {message.native_source_message_id}
            return floor

        with self.database._lock, self.database._conn:
            self.database._conn.execute(
                """INSERT INTO messaging_conversation_floor(
                       platform, account_scope, conversation_id, generation,
                       speaker_actor_ref, speaker_entity_id, opened_at, expires_at,
                       anchor_message_id, response_source_message_id, reason
                   ) VALUES (?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(platform, account_scope, conversation_id) DO UPDATE SET
                       generation=messaging_conversation_floor.generation + 1,
                       speaker_actor_ref=excluded.speaker_actor_ref,
                       speaker_entity_id=excluded.speaker_entity_id,
                       opened_at=excluded.opened_at,
                       expires_at=excluded.expires_at,
                       anchor_message_id=excluded.anchor_message_id,
                       response_source_message_id=excluded.response_source_message_id,
                       reason=excluded.reason""",
                (*key, actor_ref, speaker_entity_id, now.isoformat(), expires_at.isoformat(),
                 message.native_source_message_id, message.native_source_message_id, reason),
            )
            floor = self._row_to_floor(self._db_row(key))
        assert floor is not None
        return floor

    def continue_floor(self, message: InboundMessage, floor: _Floor, *, reason: str) -> _Floor:
        key = self.key(message)
        if self.database is None:
            current_generation = self._generation.get(key, 0)
            if current_generation != floor.generation:
                return floor
            generation = current_generation + 1
            continued = _Floor(
                generation=generation,
                speaker_actor_ref=floor.speaker_actor_ref,
                speaker_entity_id=floor.speaker_entity_id,
                opened_at=floor.opened_at,
                expires_at=floor.expires_at,
                anchor_message_id=message.native_source_message_id,
                reason=reason,
            )
            self._generation[key] = generation
            self._floors[key] = continued
            self._response_sources[key] = {message.native_source_message_id}
            return continued

        with self.database._lock, self.database._conn:
            cursor = self.database._conn.execute(
                """UPDATE messaging_conversation_floor
                   SET generation=generation + 1, anchor_message_id=?,
                       response_source_message_id=?, reason=?
                   WHERE platform=? AND account_scope=? AND conversation_id=?
                     AND generation=? AND speaker_actor_ref=? AND expires_at>=?""",
                (
                    message.native_source_message_id,
                    message.native_source_message_id,
                    reason,
                    *key,
                    floor.generation,
                    floor.speaker_actor_ref,
                    self.clock.now().isoformat(),
                ),
            )
            latest = self._row_to_floor(self._db_row(key))
        if cursor.rowcount == 1 and latest is not None:
            return latest
        return latest or floor

    def shift_away(self, message: InboundMessage) -> int:
        return self.invalidate(message.platform, message.account_scope, message.conversation_id)

    def invalidate(
        self,
        platform: MessagingPlatform | str,
        account_scope: str,
        conversation_id: str,
    ) -> int:
        key = self.raw_key(platform, account_scope, conversation_id)
        if self.database is None:
            generation = self._generation.get(key, 0) + 1
            self._generation[key] = generation
            self._floors.pop(key, None)
            self._response_sources.pop(key, None)
            return generation

        with self.database._lock, self.database._conn:
            self.database._conn.execute(
                """INSERT INTO messaging_conversation_floor(
                       platform, account_scope, conversation_id, generation
                   ) VALUES (?, ?, ?, 1)
                   ON CONFLICT(platform, account_scope, conversation_id) DO UPDATE SET
                       generation=messaging_conversation_floor.generation + 1,
                       speaker_actor_ref=NULL, speaker_entity_id=NULL, opened_at=NULL,
                       expires_at=NULL, anchor_message_id=NULL,
                       response_source_message_id=NULL, reason=NULL""",
                key,
            )
            row = self._db_row(key)
        return int(row["generation"])

    def invalidate_source(
        self,
        platform: MessagingPlatform | str,
        account_scope: str,
        conversation_id: str,
        source_message_id: str,
    ) -> tuple[int, bool]:
        key = self.raw_key(platform, account_scope, conversation_id)
        if self.database is None:
            generation = self._generation.get(key, 0)
            if source_message_id not in self._response_sources.get(key, set()):
                return generation, False
            return self.invalidate(platform, account_scope, conversation_id), True

        with self.database._lock:
            row = self._db_row(key)
            generation = int(row["generation"]) if row is not None else 0
            if row is None or row["response_source_message_id"] != source_message_id:
                return generation, False
        return self.invalidate(platform, account_scope, conversation_id), True

    def reply_lease(
        self,
        floor: _Floor,
        *,
        seconds: int = _CONTINUITY_REPLY_LEASE_SECONDS,
        cap_to_floor: bool = True,
    ) -> datetime:
        expires_at = self.clock.now() + timedelta(seconds=seconds)
        return min(floor.expires_at, expires_at) if cap_to_floor else expires_at

    def validate_reply(self, message: InboundMessage, decision: AttentionDecision) -> bool:
        if message.channel_type == ChannelType.DM:
            return decision.action == RoutingAction.RESPOND
        if decision.reply_lease_until is not None and self.clock.now() > decision.reply_lease_until:
            return False
        if self.generation(message) != decision.floor_generation:
            return False
        if any(reason in _DIRECT_ATTENTION_REASONS for reason in decision.reason_codes):
            return True
        floor = self.active(message)
        if floor is None:
            return False
        return floor.generation == decision.floor_generation


class SocialAttentionRouter:
    """Deterministic social gate before Cognitive Core."""

    def __init__(self, *, profiles: PersonProfileService, clock: Clock) -> None:
        self.profiles = profiles
        self.clock = clock
        # PersonProfileService already owns the canonical AmoraDatabase in normal
        # Core bootstrap. Reuse it instead of introducing another storage client.
        self.floor = ConversationFloor(clock, database=getattr(profiles, "store", None))

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
                reply_lease_until=self.floor.reply_lease(
                    floor, seconds=_DIRECT_REPLY_LEASE_SECONDS, cap_to_floor=False
                ),
            )

        active = self.floor.active(message)
        if active is not None and active.speaker_actor_ref == actor_ref:
            continued = self.floor.continue_floor(
                message, active, reason="GROUP_ACTIVE_FLOOR_SAME_SPEAKER"
            )
            return AttentionDecision(
                action=RoutingAction.RESPOND,
                score=0.82,
                speaker_entity_id=speaker_entity_id,
                speaker_actor_ref=actor_ref,
                addressee_id="ENTITY_AMORA",
                reason_codes=[
                    "GROUP_ACTIVE_FLOOR_SAME_SPEAKER",
                    "BOUNDED_CONTINUITY",
                    "FLOOR_GENERATION_ADVANCED",
                ],
                floor_generation=continued.generation,
                reply_lease_until=self.floor.reply_lease(continued),
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
