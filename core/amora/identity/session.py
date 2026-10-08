from __future__ import annotations

from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, Field

from amora.time import Clock


class DeviceTrust(StrEnum):
    UNKNOWN = "unknown"
    LOCAL_TRUSTED = "local_trusted"
    VERIFIED = "verified"


class PrivacyContext(StrEnum):
    PRIVATE = "PRIVATE"
    TRUSTED = "TRUSTED"
    SOCIAL = "SOCIAL"
    PUBLIC = "PUBLIC"


class DeviceIdentity(BaseModel):
    id: str
    owner_person_id: str | None = None
    device_type: str
    trust_level: DeviceTrust = DeviceTrust.UNKNOWN
    credential_ref: str | None = None
    capabilities: set[str] = Field(default_factory=set)
    last_seen: datetime


class SessionIdentity(BaseModel):
    id: str = Field(default_factory=lambda: f"SESSION_{uuid4().hex}")
    device_id: str
    authenticated_person_id: str | None = None
    privacy_context: PrivacyContext = PrivacyContext.PRIVATE
    auth_method: str = "none"
    started_at: datetime
    last_seen: datetime
    expires_at: datetime | None = None
    capabilities: set[str] = Field(default_factory=set)
    closed_at: datetime | None = None

    def is_active(self, now: datetime) -> bool:
        return self.closed_at is None and (self.expires_at is None or self.expires_at > now)


class SessionStore(Protocol):
    def upsert_device(self, device: DeviceIdentity) -> None: ...
    def get_device(self, device_id: str) -> DeviceIdentity | None: ...
    def save_session(self, session: SessionIdentity) -> None: ...
    def get_session(self, session_id: str) -> SessionIdentity | None: ...
    def close_session(self, session_id: str, closed_at: datetime) -> bool: ...
    def active_session_count(self, now: datetime) -> int: ...


class SessionService:
    def __init__(self, store: SessionStore, clock: Clock) -> None:
        self.store = store
        self.clock = clock

    def register_device(self, device: DeviceIdentity) -> None:
        self.store.upsert_device(device)

    def start_local_session(
        self,
        device_id: str,
        *,
        privacy_context: PrivacyContext = PrivacyContext.PRIVATE,
        ttl: timedelta = timedelta(hours=24),
    ) -> SessionIdentity:
        device = self.store.get_device(device_id)
        if device is None:
            raise KeyError(f"unknown device: {device_id}")
        authenticated_person_id = None
        auth_method = "none"
        if device.trust_level in {DeviceTrust.LOCAL_TRUSTED, DeviceTrust.VERIFIED} and device.owner_person_id:
            authenticated_person_id = device.owner_person_id
            auth_method = "local_api_token"
        now = self.clock.now()
        session = SessionIdentity(
            device_id=device_id,
            authenticated_person_id=authenticated_person_id,
            privacy_context=privacy_context,
            auth_method=auth_method,
            started_at=now,
            last_seen=now,
            expires_at=now + ttl,
            capabilities=set(device.capabilities),
        )
        self.store.save_session(session)
        return session

    def get(self, session_id: str) -> SessionIdentity | None:
        return self.store.get_session(session_id)

    def close(self, session_id: str) -> bool:
        return self.store.close_session(session_id, self.clock.now())
