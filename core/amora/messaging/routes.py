from __future__ import annotations

from typing import Literal
from uuid import UUID

from fastapi import Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field

from .models import (
    ExternalActorRef,
    InboundMessage,
    MessagingPlatform,
    PresenceCommand,
    PresenceState,
    TransportInvalidation,
)
from .presence_control import PresenceControlAction, PresenceControlIntent


class OutboundAckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sent: bool
    external_message_id: str | None = Field(default=None, max_length=512)
    error_code: str | None = Field(default=None, max_length=120)


class OutboundCancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    reason: str = Field(min_length=1, max_length=120)


class PresenceReportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: PresenceState
    generation: int | None = Field(default=None, ge=0)
    error_code: str | None = Field(default=None, max_length=120)
    check_complete: bool = False


class PresenceCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: PresenceCommand
    account_scope: str = Field(default="primary", min_length=1, max_length=96)


class WorkerPresenceControlRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    platform: MessagingPlatform
    account_scope: str = Field(default="primary", min_length=1, max_length=96)
    action: PresenceControlAction
    sender_identifiers: list[ExternalActorRef] = Field(min_length=1, max_length=6)


class PresenceAuthorityRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool
    confirm: bool = False


def register_messaging_routes(app, core, require_auth) -> None:
    """Register a narrow worker boundary plus operator audit/presence controls."""

    @app.post("/v1/messaging/worker/inbound", dependencies=[Depends(require_auth)])
    async def messaging_inbound(message: InboundMessage):
        return await core.messaging.receive(message)

    @app.post("/v1/messaging/worker/invalidate", dependencies=[Depends(require_auth)])
    def messaging_transport_invalidate(payload: TransportInvalidation):
        return core.messaging.invalidate_transport(payload)

    @app.get("/v1/messaging/worker/outbound", dependencies=[Depends(require_auth)])
    def messaging_outbound_claim(
        platform: MessagingPlatform,
        account_scope: str = Query(default="primary", min_length=1, max_length=96),
        limit: int = Query(default=8, ge=1, le=32),
    ):
        return {
            "messages": [
                item.model_dump(mode="json")
                for item in core.messaging_store.claim_outbound(
                    platform=platform,
                    account_scope=account_scope,
                    limit=limit,
                )
            ]
        }

    @app.post("/v1/messaging/worker/outbound/{message_id}/ack", dependencies=[Depends(require_auth)])
    def messaging_outbound_ack(message_id: UUID, payload: OutboundAckRequest):
        if not core.messaging_store.ack_outbound(
            message_id,
            sent=payload.sent,
            external_message_id=payload.external_message_id,
            error_code=payload.error_code,
        ):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="outbound message not claimable")
        return {"status": "ok"}

    @app.post("/v1/messaging/worker/outbound/{message_id}/cancel", dependencies=[Depends(require_auth)])
    def messaging_outbound_cancel(message_id: UUID, payload: OutboundCancelRequest):
        if not core.messaging_store.cancel_outbound(message_id, reason=payload.reason):
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="outbound message not cancellable")
        return {"status": "ok"}

    @app.get("/v1/messaging/worker/presence/{platform}", dependencies=[Depends(require_auth)])
    def messaging_presence_worker_get(
        platform: MessagingPlatform,
        account_scope: str = Query(default="primary", min_length=1, max_length=96),
    ):
        return core.platform_presence.snapshot(platform, account_scope).model_dump(mode="json")

    @app.post("/v1/messaging/worker/presence/{platform}/report", dependencies=[Depends(require_auth)])
    def messaging_presence_worker_report(
        platform: MessagingPlatform,
        payload: PresenceReportRequest,
        account_scope: str = Query(default="primary", min_length=1, max_length=96),
    ):
        try:
            snapshot = core.platform_presence.report(
                platform,
                payload.state,
                account_scope=account_scope,
                generation=payload.generation,
                error_code=payload.error_code,
                check_complete=payload.check_complete,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return snapshot.model_dump(mode="json")

    @app.post("/v1/messaging/worker/presence-control", dependencies=[Depends(require_auth)])
    def messaging_worker_presence_control(payload: WorkerPresenceControlRequest):
        speaker_entity_id = None
        for ref in sorted(payload.sender_identifiers, key=lambda item: item.confidence, reverse=True):
            entity = core.profiles.resolve_actor(
                platform=payload.platform.value,
                identifier_type=ref.identifier_type,
                external_actor_id=ref.external_actor_id,
                account_scope=payload.account_scope,
            )
            if entity is not None:
                speaker_entity_id = entity.id
                break
        outcome = core.messaging.presence_control.execute_intent(
            PresenceControlIntent(platform=payload.platform, action=payload.action),
            speaker_entity_id=speaker_entity_id,
            account_scope=payload.account_scope,
        )
        return {
            "authorized": outcome.authorized,
            "executed": outcome.executed,
            "response_text": outcome.response_text,
            "platform": outcome.intent.platform.value,
            "action": outcome.intent.action.value,
            "speaker_entity_id": speaker_entity_id,
            "snapshot": outcome.snapshot.model_dump(mode="json") if outcome.snapshot else None,
        }

    @app.get("/v1/operator/messaging/audit", dependencies=[Depends(require_auth)])
    def messaging_operator_audit(
        platform: MessagingPlatform | None = None,
        conversation_id: str | None = Query(default=None, max_length=512),
        limit: int = Query(default=100, ge=1, le=500),
        reveal_private: Literal["false", "true"] = "false",
    ):
        items = core.messaging_store.list_audit(
            platform=platform,
            conversation_id=conversation_id,
            limit=limit,
        )
        reveal = reveal_private == "true"
        rows = []
        for item in items:
            data = item.model_dump(mode="json")
            if not reveal:
                data["sender_external_id"] = _mask_identifier(item.sender_external_id)
                data["conversation_id"] = _mask_identifier(item.conversation_id)
                data["mentions"] = [_mask_identifier(value) for value in item.mentions]
            rows.append(data)
        return {"records": rows, "private_revealed": reveal}

    @app.get("/v1/operator/messaging/presence", dependencies=[Depends(require_auth)])
    def messaging_operator_presence():
        return {
            "platforms": [
                item.model_dump(mode="json") for item in core.messaging_store.list_presence()
            ],
            "authority_ids": sorted(core.profiles.platform_presence_authorities()),
        }

    @app.post("/v1/operator/messaging/presence/{platform}", dependencies=[Depends(require_auth)])
    def messaging_operator_presence_command(
        platform: MessagingPlatform,
        payload: PresenceCommandRequest,
    ):
        return core.platform_presence.request(
            platform,
            payload.command,
            account_scope=payload.account_scope,
        ).model_dump(mode="json")

    @app.put(
        "/v1/operator/messaging/presence-authority/{entity_id}",
        dependencies=[Depends(require_auth)],
    )
    def messaging_operator_presence_authority(
        entity_id: str,
        payload: PresenceAuthorityRequest,
    ):
        if not payload.confirm:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="platform presence authority change requires explicit confirmation",
            )
        profile = core.profiles.profile(entity_id)
        if profile is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="person profile not found")
        if profile.can_control_platform_presence == payload.enabled:
            return profile.model_dump(mode="json")
        saved = core.profiles.upsert_profile(
            profile.model_copy(update={"can_control_platform_presence": payload.enabled}),
            source_revision="operator:web:platform-presence-authority",
        )
        return saved.model_dump(mode="json")


def _mask_identifier(value: str | None) -> str | None:
    if value is None:
        return None
    if len(value) <= 4:
        return "••••"
    return "••••" + value[-4:]
