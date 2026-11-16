"""MM.00D.3A.2 — internal consent for native messaging participants.

All entry points assume an authenticated transport and fresh roster. Native
references are never canonical person IDs. This module starts no media I/O.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from threading import RLock
from uuid import UUID, uuid4

from amora.identity.models import EntityType
from amora.identity.profiles import PersonProfileService
from amora.messaging.models import ChannelType, MessagingPlatform
from amora.messaging.session_identity import messaging_session_ref

from .models import PrivacyScope
from .multimodal import ObservationContext, ObservationSource, SensoryModality


@dataclass(frozen=True, slots=True)
class NativeMember:
    identifier_type: str
    external_actor_id: str


@dataclass(frozen=True, slots=True)
class PlatformSession:
    id: UUID
    platform: MessagingPlatform
    account_scope: str
    conversation_id: str
    session_id: str
    channel_type: ChannelType
    privacy_scope: PrivacyScope
    expires_at: datetime
    scope_id: UUID
    members: tuple[tuple[NativeMember, str], ...]


@dataclass(slots=True)
class _ActiveSession:
    session: PlatformSession
    consent: dict[str, tuple[frozenset[SensoryModality], bool]] = field(default_factory=dict)
    streams: dict[tuple[str, SensoryModality], UUID] = field(default_factory=dict)


class PlatformSensoryAuthority:
    """Bounded internal roster/consent registry, fail-closed after restart.

    No raw IDs appear in ObservationContext. Roster and principal must be
    authenticated transport facts; a public request cannot assert consent.
    """

    def __init__(self, profiles: PersonProfileService, *, max_sessions: int = 32,
                 max_ttl: timedelta = timedelta(minutes=30)):
        if type(max_sessions) is not int or not 1 <= max_sessions <= 128:
            raise ValueError("PLATFORM_SENSORY_CAPACITY_INVALID")
        if not isinstance(max_ttl, timedelta) or not timedelta(0) < max_ttl <= timedelta(hours=2):
            raise ValueError("PLATFORM_SENSORY_TTL_INVALID")
        self.profiles = profiles
        self.clock = profiles.clock
        self.max_sessions = max_sessions
        self.max_ttl = max_ttl
        self._sessions: dict[UUID, _ActiveSession] = {}
        self._lock = RLock()

    @staticmethod
    def _native_valid(platform, member):
        if not isinstance(member, NativeMember) or not isinstance(member.external_actor_id, str):
            return False
        if platform == MessagingPlatform.DISCORD:
            return member.identifier_type == "discord_user_id" and bool(
                re.fullmatch(r"[0-9]{15,22}", member.external_actor_id))
        if platform == MessagingPlatform.WHATSAPP:
            suffix = {"whatsapp_jid": r"s\.whatsapp\.net", "whatsapp_lid": "lid"}.get(member.identifier_type)
            return suffix is not None and bool(re.fullmatch(r"[0-9]{1,32}@" + suffix, member.external_actor_id))
        return False

    def _resolve(self, platform, account_scope, member):
        if not self._native_valid(platform, member):
            raise ValueError("PLATFORM_SENSORY_NATIVE_INVALID")
        entity = self.profiles.resolve_actor(
            platform=platform.value, identifier_type=member.identifier_type,
            external_actor_id=member.external_actor_id, account_scope=account_scope,
        )
        if entity is None or entity.entity_type != EntityType.PERSON:
            raise ValueError("PLATFORM_SENSORY_IDENTITY_UNVERIFIED")
        args = dict(entity_id=entity.id, identifier_type=member.identifier_type,
                    external_actor_id=member.external_actor_id, account_scope=account_scope)
        if platform == MessagingPlatform.DISCORD:
            verified = self.profiles.has_verified_discord_binding(**args)
        else:
            verified = self.profiles.store.has_verified_platform_binding(platform=platform.value, **args)
        if not verified:
            raise ValueError("PLATFORM_SENSORY_IDENTITY_UNVERIFIED")
        return entity.id

    def _prune(self, now):
        for key, active in tuple(self._sessions.items()):
            if active.session.expires_at <= now:
                del self._sessions[key]

    def _roster(self, platform, account_scope, members):
        if not isinstance(members, tuple) or not 1 <= len(members) <= 16:
            raise ValueError("PLATFORM_SENSORY_ROSTER_INVALID")
        resolved = tuple((member, self._resolve(platform, account_scope, member)) for member in members)
        if len({person for _, person in resolved}) != len(resolved):
            raise ValueError("PLATFORM_SENSORY_ROSTER_INVALID")
        return tuple(sorted(resolved, key=lambda item: item[1]))

    def open(self, *, platform: MessagingPlatform, account_scope: str,
             conversation_id: str, channel_type: ChannelType,
             members: tuple[NativeMember, ...], privacy_scope: PrivacyScope,
             ttl: timedelta = timedelta(minutes=15)) -> PlatformSession:
        if (not isinstance(platform, MessagingPlatform) or not isinstance(channel_type, ChannelType)
                or not isinstance(privacy_scope, PrivacyScope)
                or not isinstance(account_scope, str) or not 1 <= len(account_scope) <= 96
                or not isinstance(conversation_id, str) or not 1 <= len(conversation_id) <= 512
                or "|" in account_scope or "|" in conversation_id):
            raise ValueError("PLATFORM_SENSORY_SCOPE_INVALID")
        # Only a human peer in a bot DM, or the complete human roster of a group.
        if channel_type == ChannelType.DM:
            if privacy_scope not in {PrivacyScope.PERSONAL, PrivacyScope.PRIVATE} or len(members) != 1:
                raise ValueError("PLATFORM_SENSORY_SCOPE_INVALID")
        elif privacy_scope != PrivacyScope.SOCIAL:
            raise ValueError("PLATFORM_SENSORY_SCOPE_INVALID")
        if not isinstance(ttl, timedelta) or not timedelta(0) < ttl <= self.max_ttl:
            raise ValueError("PLATFORM_SENSORY_TTL_INVALID")
        with self._lock:
            now = self.clock.now()
            self._prune(now)
            if len(self._sessions) >= self.max_sessions:
                raise ValueError("PLATFORM_SENSORY_CAPACITY")
            roster = self._roster(platform, account_scope, members)
            session = PlatformSession(
                id=uuid4(), platform=platform, account_scope=account_scope,
                conversation_id=conversation_id,
                session_id=messaging_session_ref(platform.value, account_scope, conversation_id),
                channel_type=channel_type, privacy_scope=privacy_scope,
                expires_at=now + ttl, scope_id=uuid4(), members=roster,
            )
            self._sessions[session.id] = _ActiveSession(session)
            return session

    def _active(self, session_id, *, platform, account_scope, conversation_id, current_members):
        self._prune(self.clock.now())
        active = self._sessions.get(session_id)
        if active is None:
            raise ValueError("PLATFORM_SENSORY_SESSION_UNAUTHORIZED")
        session = active.session
        if (session.platform, session.account_scope, session.conversation_id) != (platform, account_scope, conversation_id):
            raise ValueError("PLATFORM_SENSORY_SCOPE_MISMATCH")
        try:
            if self._roster(session.platform, account_scope, current_members) != session.members:
                raise ValueError("PLATFORM_SENSORY_ROSTER_CHANGED")
        except ValueError:
            self._sessions.pop(session_id, None)
            raise
        return active

    @staticmethod
    def _participant(active, member):
        person = dict(active.session.members).get(member)
        if person is None:
            raise ValueError("PLATFORM_SENSORY_PARTICIPANT_UNAUTHORIZED")
        return person

    def consent(self, session_id: UUID, *, platform: MessagingPlatform, account_scope: str,
                conversation_id: str, current_members: tuple[NativeMember, ...],
                authenticated_member: NativeMember, modalities: frozenset[SensoryModality],
                explicit_consent: bool, remote_processing_allowed: bool = False) -> None:
        if explicit_consent is not True:
            raise ValueError("PLATFORM_SENSORY_CONSENT_REQUIRED")
        if (type(remote_processing_allowed) is not bool or not isinstance(modalities, frozenset)
                or not modalities or any(not isinstance(m, SensoryModality) or
                m not in {SensoryModality.SPEECH, SensoryModality.IMAGE} for m in modalities)):
            raise ValueError("PLATFORM_SENSORY_POLICY_INVALID")
        with self._lock:
            active = self._active(session_id, platform=platform, account_scope=account_scope,
                                  conversation_id=conversation_id, current_members=current_members)
            person = self._participant(active, authenticated_member)
            if remote_processing_allowed and active.session.privacy_scope == PrivacyScope.PRIVATE:
                raise ValueError("PLATFORM_SENSORY_REMOTE_FORBIDDEN")
            policy = (modalities, remote_processing_allowed)
            previous = active.consent.get(person)
            if previous is not None and previous != policy:
                # A policy change requires new session/correlation, not widening
                # existing stream permissions under the same delivery identity.
                self._sessions.pop(session_id, None)
                raise ValueError("PLATFORM_SENSORY_POLICY_CHANGED")
            active.consent[person] = policy

    def authorize(self, session_id: UUID, *, platform: MessagingPlatform, account_scope: str,
                  conversation_id: str, current_members: tuple[NativeMember, ...],
                  authenticated_member: NativeMember, modality: SensoryModality,
                  for_remote_processing: bool = False) -> ObservationContext:
        if not isinstance(modality, SensoryModality) or type(for_remote_processing) is not bool:
            raise ValueError("PLATFORM_SENSORY_POLICY_INVALID")
        with self._lock:
            active = self._active(session_id, platform=platform, account_scope=account_scope,
                                  conversation_id=conversation_id, current_members=current_members)
            person = self._participant(active, authenticated_member)
            audience = tuple(sorted(p for _, p in active.session.members))
            if any(p not in active.consent or modality not in active.consent[p][0] for p in audience):
                raise ValueError("PLATFORM_SENSORY_CONSENT_INCOMPLETE")
            remote = all(active.consent[p][1] for p in audience)
            if for_remote_processing and not remote:
                raise ValueError("PLATFORM_SENSORY_REMOTE_FORBIDDEN")
            stream = active.streams.setdefault((person, modality), uuid4())
            return ObservationContext(
                source=ObservationSource(platform.value), session_id=active.session.session_id,
                scope_id=active.session.scope_id, correlation_id=active.session.id,
                stream_id=stream, epoch=0, actor_id=person, owner_entity_id=person,
                audience=audience, privacy_scope=active.session.privacy_scope,
                remote_processing_allowed=remote,
            )

    def revoke(self, session_id: UUID, *, platform: MessagingPlatform, account_scope: str,
               conversation_id: str, current_members: tuple[NativeMember, ...],
               authenticated_member: NativeMember) -> None:
        with self._lock:
            active = self._active(session_id, platform=platform, account_scope=account_scope,
                                  conversation_id=conversation_id, current_members=current_members)
            self._participant(active, authenticated_member)
            self._sessions.pop(session_id, None)
