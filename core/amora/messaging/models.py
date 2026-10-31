from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from amora.events import PrivacyScope


class MessagingPlatform(StrEnum):
    DISCORD = "discord"
    WHATSAPP = "whatsapp"


class ChannelType(StrEnum):
    DM = "dm"
    GROUP = "group"


class RoutingAction(StrEnum):
    BLOCK = "block"
    QUARANTINE = "quarantine"
    CONTEXT_ONLY = "context_only"
    RESPOND = "respond"
    SILENCE = "silence"


class AuditDirection(StrEnum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class OutboundStatus(StrEnum):
    QUEUED = "queued"
    CLAIMED = "claimed"
    SENT = "sent"
    FAILED = "failed"
    CANCELLED = "cancelled"


class PresenceState(StrEnum):
    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    CHECKING = "checking"
    DISCONNECTING = "disconnecting"
    ERROR = "error"


class PresenceCommand(StrEnum):
    CONNECT = "connect"
    DISCONNECT = "disconnect"
    CHECK = "check"


class ExternalActorRef(BaseModel):
    """One stable platform identifier presented by an adapter.

    A platform identifier is evidence for resolving a canonical PERSON. It is not
    itself a person id and must never be promoted into EntityRegistry implicitly.
    """

    model_config = ConfigDict(extra="forbid")

    identifier_type: str = Field(min_length=1, max_length=64)
    external_actor_id: str = Field(min_length=1, max_length=256)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)


class AttachmentRef(BaseModel):
    model_config = ConfigDict(extra="forbid")

    attachment_id: str = Field(min_length=1, max_length=256)
    media_type: str | None = Field(default=None, max_length=128)
    filename: str | None = Field(default=None, max_length=256)
    size_bytes: int | None = Field(default=None, ge=0, le=128 * 1024 * 1024)


class InboundMessage(BaseModel):
    """Provider/platform-independent inbound envelope.

    Adapters may inspect platform-native payloads, but Cognitive Core only sees
    this normalized contract after identity and social-attention gates pass.
    """

    model_config = ConfigDict(extra="forbid")

    platform: MessagingPlatform
    account_scope: str = Field(default="primary", min_length=1, max_length=96)
    conversation_id: str = Field(min_length=1, max_length=512)
    message_id: str = Field(min_length=1, max_length=512)
    channel_type: ChannelType
    sender_identifiers: list[ExternalActorRef] = Field(min_length=1, max_length=6)
    sender_display_name: str | None = Field(default=None, max_length=160)
    sender_username: str | None = Field(default=None, max_length=160)
    text: str = Field(default="", max_length=12000)
    mentioned_actor_ids: list[str] = Field(default_factory=list, max_length=64)
    mentions_amora: bool = False
    reply_to_message_id: str | None = Field(default=None, max_length=512)
    reply_to_amora: bool = False
    reply_to_author_identifiers: list[ExternalActorRef] = Field(default_factory=list, max_length=6)
    attachments: list[AttachmentRef] = Field(default_factory=list, max_length=16)
    timestamp: datetime
    privacy_scope: PrivacyScope = PrivacyScope.PERSONAL
    remote_processing_allowed: bool = False
    native_thread_id: str | None = Field(default=None, max_length=512)
    guild_or_group_id: str | None = Field(default=None, max_length=512)

    @model_validator(mode="after")
    def require_content(self) -> "InboundMessage":
        if not self.text.strip() and not self.attachments:
            raise ValueError("inbound message requires text or attachment metadata")
        return self


class AttentionDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: RoutingAction
    score: float = Field(default=0.0, ge=0.0, le=1.0)
    speaker_entity_id: str | None = None
    speaker_actor_ref: str
    addressee_id: str | None = None
    reason_codes: list[str] = Field(default_factory=list, max_length=32)
    floor_generation: int = Field(default=0, ge=0)
    reply_lease_until: datetime | None = None


class OutboundMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    platform: MessagingPlatform
    account_scope: str = Field(default="primary", min_length=1, max_length=96)
    conversation_id: str = Field(min_length=1, max_length=512)
    target_entity_id: str | None = None
    text: str = Field(min_length=1, max_length=12000)
    reply_to_message_id: str | None = Field(default=None, max_length=512)
    mention_actor_ids: list[str] = Field(default_factory=list, max_length=32)
    source_message_id: str | None = Field(default=None, max_length=512)
    created_at: datetime
    status: OutboundStatus = OutboundStatus.QUEUED
    metadata: dict[str, Any] = Field(default_factory=dict)


class MessagingAuditRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    platform: MessagingPlatform
    account_scope: str
    conversation_id: str
    message_id: str
    direction: AuditDirection
    channel_type: ChannelType
    sender_external_id: str | None = None
    speaker_entity_id: str | None = None
    text: str = ""
    reply_to_message_id: str | None = None
    reply_to_author_entity_id: str | None = None
    mentions: list[str] = Field(default_factory=list)
    routing_action: RoutingAction | None = None
    reason_codes: list[str] = Field(default_factory=list)
    responded: bool = False
    timestamp: datetime
    metadata: dict[str, Any] = Field(default_factory=dict)


class PresenceSnapshot(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: MessagingPlatform
    account_scope: str = "primary"
    state: PresenceState = PresenceState.DISCONNECTED
    desired_connected: bool = False
    check_requested: bool = False
    generation: int = Field(default=0, ge=0)
    updated_at: datetime
    last_connected_at: datetime | None = None
    last_disconnected_at: datetime | None = None
    last_error_code: str | None = Field(default=None, max_length=120)


class InboundResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: AttentionDecision
    duplicate: bool = False
    outbound: list[OutboundMessage] = Field(default_factory=list)
