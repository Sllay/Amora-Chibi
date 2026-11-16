"""MM.00D.3A.1 — internal, fail-closed Web/device sensory consent.

An authenticated transport must verify its principal before invoking issue().
This authority is deliberately process-local: restart revokes all grants.
No capture, provider, persistence or external route is started here.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from threading import RLock
from uuid import UUID, uuid4

from amora.identity.session import DeviceTrust, PrivacyContext, SessionService

from .models import PrivacyScope
from .multimodal import ObservationContext, ObservationSource, SensoryModality


@dataclass(frozen=True, slots=True)
class SensoryGrant:
    grant_id: UUID
    session_id: str
    person_id: str
    device_id: str
    privacy_scope: PrivacyScope
    remote_processing_allowed: bool
    expires_at: datetime
    scope_id: UUID
    correlation_id: UUID
    streams: tuple[tuple[SensoryModality, UUID], ...]


class DeviceSensoryAuthority:
    """Bounded grant registry for a single authenticated device owner.

    A grant authorizes modalities, not storage or automatic recording.
    Callers must check again at provider and publication boundaries. The
    returned context is not a transferable credential or durable grant.
    """

    def __init__(self, sessions: SessionService, *, max_grants: int = 64,
                 max_ttl: timedelta = timedelta(minutes=30)):
        if type(max_grants) is not int or not 1 <= max_grants <= 1024:
            raise ValueError("SENSORY_GRANT_LIMIT_INVALID")
        if not timedelta(0) < max_ttl <= timedelta(hours=2):
            raise ValueError("SENSORY_GRANT_TTL_INVALID")
        self.sessions = sessions
        self.clock = sessions.clock
        self.max_grants = max_grants
        self.max_ttl = max_ttl
        self._grants: dict[UUID, SensoryGrant] = {}
        self._lock = RLock()

    def _session(self, session_id: str, person_id: str, now: datetime):
        session = self.sessions.get(session_id)
        if (session is None or not session.is_active(now)
                or session.authenticated_person_id != person_id
                or session.auth_method != "local_api_token"
                or session.privacy_context not in {PrivacyContext.PRIVATE, PrivacyContext.TRUSTED}):
            raise ValueError("SENSORY_SESSION_UNAUTHORIZED")
        device = self.sessions.store.get_device(session.device_id)
        if (device is None or device.owner_person_id != person_id
                or device.trust_level not in {DeviceTrust.LOCAL_TRUSTED, DeviceTrust.VERIFIED}):
            raise ValueError("SENSORY_DEVICE_UNAUTHORIZED")
        return session

    @staticmethod
    def _scope(session) -> PrivacyScope:
        return (PrivacyScope.PRIVATE if session.privacy_context == PrivacyContext.PRIVATE
                else PrivacyScope.PERSONAL)

    def _prune(self, now: datetime) -> None:
        for grant_id, grant in tuple(self._grants.items()):
            if grant.expires_at <= now:
                del self._grants[grant_id]

    def issue(self, *, session_id: str, authenticated_person_id: str,
              modalities: frozenset[SensoryModality], explicit_consent: bool,
              remote_processing_allowed: bool = False,
              ttl: timedelta = timedelta(minutes=15)) -> SensoryGrant:
        """Issue only after authenticated owner explicitly consents to capture.

        Never call with an actor identifier taken from worker-controlled data.
        No public API accepts this object or its consent flag.
        """
        if explicit_consent is not True:
            raise ValueError("SENSORY_CONSENT_REQUIRED")
        if type(remote_processing_allowed) is not bool:
            raise ValueError("SENSORY_POLICY_INVALID")
        if (not isinstance(modalities, frozenset) or not modalities
                or any(not isinstance(m, SensoryModality) for m in modalities)):
            raise ValueError("SENSORY_MODALITY_INVALID")
        if not isinstance(ttl, timedelta) or not timedelta(0) < ttl <= self.max_ttl:
            raise ValueError("SENSORY_GRANT_TTL_INVALID")
        with self._lock:
            now = self.clock.now()
            self._prune(now)
            session = self._session(session_id, authenticated_person_id, now)
            scope = self._scope(session)
            if remote_processing_allowed and scope != PrivacyScope.PERSONAL:
                raise ValueError("SENSORY_REMOTE_FORBIDDEN")
            if len(self._grants) >= self.max_grants:
                raise ValueError("SENSORY_GRANT_CAPACITY")
            expires = min(now + ttl, session.expires_at) if session.expires_at else now + ttl
            grant = SensoryGrant(
                grant_id=uuid4(), session_id=session.id, person_id=authenticated_person_id,
                device_id=session.device_id, privacy_scope=scope,
                remote_processing_allowed=remote_processing_allowed, expires_at=expires,
                scope_id=uuid4(), correlation_id=uuid4(),
                streams=tuple((m, uuid4()) for m in sorted(modalities, key=lambda m: m.value)),
            )
            self._grants[grant.grant_id] = grant
            return grant

    def authorize(self, grant_id: UUID, *, authenticated_person_id: str,
                  modality: SensoryModality, for_remote_processing: bool = False) -> ObservationContext:
        """Revalidate the exact grant before every sensitive operation."""
        if not isinstance(modality, SensoryModality) or type(for_remote_processing) is not bool:
            raise ValueError("SENSORY_POLICY_INVALID")
        with self._lock:
            now = self.clock.now()
            self._prune(now)
            grant = self._grants.get(grant_id)
            if grant is None or grant.person_id != authenticated_person_id:
                raise ValueError("SENSORY_GRANT_UNAUTHORIZED")
            try:
                session = self._session(grant.session_id, grant.person_id, now)
                if session.device_id != grant.device_id or self._scope(session) != grant.privacy_scope:
                    raise ValueError("SENSORY_SESSION_CHANGED")
            except ValueError:
                # A revoked or changed underlying authority must not revive
                # merely because a later caller restores the old device state.
                self._grants.pop(grant_id, None)
                raise
            if for_remote_processing and not grant.remote_processing_allowed:
                raise ValueError("SENSORY_REMOTE_FORBIDDEN")
            stream_id = dict(grant.streams).get(modality)
            if stream_id is None:
                raise ValueError("SENSORY_MODALITY_FORBIDDEN")
            return ObservationContext(
                source=ObservationSource.SCREEN if modality == SensoryModality.SCREEN else ObservationSource.WEB,
                session_id=grant.session_id, scope_id=grant.scope_id,
                correlation_id=grant.correlation_id, stream_id=stream_id, epoch=0,
                actor_id=grant.person_id if modality == SensoryModality.SPEECH else None,
                owner_entity_id=grant.person_id, audience=(grant.person_id,),
                privacy_scope=grant.privacy_scope,
                remote_processing_allowed=grant.remote_processing_allowed,
            )

    def revoke(self, grant_id: UUID, *, authenticated_person_id: str) -> bool:
        with self._lock:
            grant = self._grants.get(grant_id)
            if grant is None or grant.person_id != authenticated_person_id:
                return False
            del self._grants[grant_id]
            return True
