"""Authenticated worker protocol. Installed routes are inert without a bridge."""
from typing import Literal
from uuid import UUID
from fastapi import Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator


class RealtimeOperation(BaseModel):
    model_config=ConfigDict(extra="forbid")
    action: Literal["authorize","prepare","confirm","reject","ack"]
    account: str=Field(min_length=1,max_length=96)
    turn_id: UUID
    binding: str=Field(pattern=r"^[0-9a-f]{64}$")
    index: int=Field(default=0,ge=0,le=31)
    seq: int=Field(default=1,ge=1)
    digest: str | None=Field(default=None,pattern=r"^[0-9a-f]{64}$")
    terminal: bool=False
    token: str | None=Field(default=None,max_length=64)
    message_id: str | None=Field(default=None,pattern=r"^[0-9]{17,20}$")
    outbound_id: UUID | None=None

    @model_validator(mode="after")
    def required_fields(self):
        if self.action in {"authorize","prepare","confirm"} and self.digest is None:
            raise ValueError("REALTIME_DIGEST_REQUIRED")
        if self.action in {"confirm","reject"} and not self.token:
            raise ValueError("REALTIME_TOKEN_REQUIRED")
        if self.action in {"confirm","ack"} and not self.message_id:
            raise ValueError("REALTIME_RECEIPT_REQUIRED")
        if self.action=="ack" and self.outbound_id is None:
            raise ValueError("REALTIME_OUTBOUND_REQUIRED")
        return self


def register_realtime_routes(app,core,require_auth):
    @app.get("/v1/messaging/worker/realtime/next",dependencies=[Depends(require_auth)])
    async def realtime_next(account: str=Query(min_length=1,max_length=96)):
        bridge=core.messaging.realtime_bridge
        return await bridge.next(account) if bridge is not None else {"draft":None}

    @app.get("/v1/messaging/worker/realtime/final/{outbound_id}",dependencies=[Depends(require_auth)])
    async def realtime_final(outbound_id: UUID,account: str=Query(min_length=1,max_length=96)):
        bridge=core.messaging.realtime_bridge
        if bridge is None:
            raise HTTPException(status_code=409,detail="REALTIME_DISABLED")
        result=await bridge.final(outbound_id,account)
        if result is None:
            raise HTTPException(status_code=409,detail="REALTIME_FINAL_NOT_AUTHORIZED")
        return result

    @app.post("/v1/messaging/worker/realtime/operation",dependencies=[Depends(require_auth)])
    async def realtime_operation(op: RealtimeOperation):
        bridge=core.messaging.realtime_bridge
        if bridge is None:
            raise HTTPException(status_code=409,detail="REALTIME_DISABLED")
        return await bridge.operation(op)
