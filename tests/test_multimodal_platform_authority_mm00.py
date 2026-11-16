"""MM.00D.3A.2 — native bindings and per-person platform consent."""
from datetime import timedelta

import pytest

from amora.events import PrivacyScope
from amora.events.multimodal import ObservationSource, SensoryModality
from amora.events.platform_sensory_authority import NativeMember, PlatformSensoryAuthority
from amora.identity.models import Entity, EntityType, build_initial_registry
from amora.identity.profiles import BindingTrust, PersonProfileService, PlatformIdentityBinding, PlatformKind
from amora.messaging.models import ChannelType, MessagingPlatform
from amora.messaging.session_identity import messaging_session_ref
from amora.storage import AmoraDatabase
from amora.time import SimulatedClock
from test_multimodal_contract_mm00 import NOW


BK = NativeMember("discord_user_id", "123456789012345678")
OTHER = NativeMember("discord_user_id", "223456789012345678")
WA = NativeMember("whatsapp_jid", "5511999999999@s.whatsapp.net")
WA_LID = NativeMember("whatsapp_lid", "123456789@lid")


@pytest.fixture
def setup(tmp_path):
    db = AmoraDatabase(tmp_path / "platform.db")
    db.migrate()
    registry = build_initial_registry()
    registry.add_entity(Entity(id="PERSON_OTHER", canonical_name="Pessoa teste", entity_type=EntityType.PERSON))
    db.save_registry(registry)
    clock = SimulatedClock(NOW)
    profiles = PersonProfileService(store=db, identities=registry, clock=clock,
                                    persist_registry=lambda: db.save_registry(registry))
    for platform, member, person in (
        (PlatformKind.DISCORD, BK, "PERSON_BK"),
        (PlatformKind.DISCORD, OTHER, "PERSON_OTHER"),
        (PlatformKind.WHATSAPP, WA, "PERSON_BK"),
        (PlatformKind.WHATSAPP, WA_LID, "PERSON_BK"),
    ):
        db.upsert_platform_identity_binding(PlatformIdentityBinding(
            platform=platform, identifier_type=member.identifier_type,
            external_actor_id=member.external_actor_id, entity_id=person,
        ), updated_at=NOW)
    try:
        yield db, clock, profiles
    finally:
        db.close()


def open_group(authority, **overrides):
    return authority.open(**{
        "platform": MessagingPlatform.DISCORD, "account_scope": "primary",
        "conversation_id": "voice-channel", "channel_type": ChannelType.GROUP,
        "members": (BK, OTHER), "privacy_scope": PrivacyScope.SOCIAL, **overrides,
    })


def args(session, member, **overrides):
    return {
        "platform": session.platform, "account_scope": session.account_scope,
        "conversation_id": session.conversation_id,
        "current_members": tuple(m for m, _ in session.members),
        "authenticated_member": member, **overrides,
    }


def grant(authority, session, member, **overrides):
    authority.consent(session.id, **args(session, member, **{
        "modalities": frozenset({SensoryModality.SPEECH}),
        "explicit_consent": True, **overrides,
    }))


def authorize(authority, session, member, **overrides):
    return authority.authorize(session.id, **args(session, member, **{
        "modality": SensoryModality.SPEECH, **overrides,
    }))


def test_group_requires_individual_consent_and_preserves_speakers(setup):
    db, _, profiles = setup
    authority = PlatformSensoryAuthority(profiles)
    session = open_group(authority)
    assert session.session_id == messaging_session_ref("discord", "primary", "voice-channel")
    grant(authority, session, BK, remote_processing_allowed=True)
    with pytest.raises(ValueError, match="CONSENT_INCOMPLETE"):
        authorize(authority, session, BK)
    grant(authority, session, OTHER)
    first = authorize(authority, session, BK)
    second = authorize(authority, session, OTHER)
    assert first.actor_id == first.owner_entity_id == "PERSON_BK"
    assert second.actor_id == second.owner_entity_id == "PERSON_OTHER"
    assert first.audience == second.audience == ("PERSON_BK", "PERSON_OTHER")
    assert first.privacy_scope == PrivacyScope.SOCIAL
    assert not first.remote_processing_allowed and not second.remote_processing_allowed
    assert first.correlation_id == second.correlation_id == session.id
    assert first.stream_id != second.stream_id
    assert authorize(authority, session, BK).stream_id == first.stream_id
    assert first.source == ObservationSource.DISCORD
    with pytest.raises(ValueError, match="REMOTE_FORBIDDEN"):
        authorize(authority, session, BK, for_remote_processing=True)
    with pytest.raises(ValueError, match="CONSENT_INCOMPLETE"):
        authorize(authority, session, BK, modality=SensoryModality.IMAGE)
    assert db.event_count() == 0
    # Policy widening cannot mutate an already-issued stream.
    with pytest.raises(ValueError, match="POLICY_CHANGED"):
        grant(authority, session, OTHER, remote_processing_allowed=True)
    with pytest.raises(ValueError, match="SESSION_UNAUTHORIZED"):
        authorize(authority, session, BK)


def test_scope_and_roster_are_isolated(setup):
    _, _, profiles = setup
    authority = PlatformSensoryAuthority(profiles)
    session = open_group(authority)
    grant(authority, session, BK, remote_processing_allowed=True)
    grant(authority, session, OTHER, remote_processing_allowed=True)
    assert authorize(authority, session, BK, for_remote_processing=True).remote_processing_allowed
    for wrong in ({"account_scope": "other"}, {"conversation_id": "other"}, {"platform": MessagingPlatform.WHATSAPP}):
        with pytest.raises(ValueError, match="SCOPE_MISMATCH"):
            authorize(authority, session, BK, **wrong)
    with pytest.raises(ValueError, match="PARTICIPANT_UNAUTHORIZED"):
        authorize(authority, session, NativeMember("discord_user_id", "323456789012345678"))
    assert authorize(authority, session, BK).actor_id == "PERSON_BK"
    with pytest.raises(ValueError, match="ROSTER_CHANGED"):
        authorize(authority, session, BK, current_members=(BK,))
    with pytest.raises(ValueError, match="SESSION_UNAUTHORIZED"):
        authorize(authority, session, BK)


@pytest.mark.parametrize("members,reason", [
    ((NativeMember("discord_username", "example"),), "NATIVE_INVALID"),
    ((NativeMember("discord_user_id", "PERSON_BK"),), "NATIVE_INVALID"),
    ((NativeMember("discord_user_id", "323456789012345678"),), "IDENTITY_UNVERIFIED"),
    ((BK, BK), "ROSTER_INVALID"),
])
def test_native_identity_not_display_name_or_canonical_claim(setup, members, reason):
    _, _, profiles = setup
    with pytest.raises(ValueError, match=reason):
        open_group(PlatformSensoryAuthority(profiles), members=members)


def test_binding_revocation_and_observed_trust(setup):
    db, _, profiles = setup
    authority = PlatformSensoryAuthority(profiles)
    session = open_group(authority)
    grant(authority, session, BK)
    grant(authority, session, OTHER)
    db.upsert_platform_identity_binding(PlatformIdentityBinding(
        platform=PlatformKind.DISCORD, identifier_type=OTHER.identifier_type,
        external_actor_id=OTHER.external_actor_id, entity_id="PERSON_OTHER",
        trust=BindingTrust.OBSERVED,
    ), updated_at=NOW)
    with pytest.raises(ValueError, match="IDENTITY_UNVERIFIED"):
        authorize(authority, session, BK)
    with pytest.raises(ValueError, match="SESSION_UNAUTHORIZED"):
        authorize(authority, session, BK)
    with pytest.raises(ValueError, match="IDENTITY_UNVERIFIED"):
        open_group(authority)


@pytest.mark.parametrize("member", [WA, WA_LID])
def test_whatsapp_native_dm_private_and_revoke(setup, member):
    _, _, profiles = setup
    authority = PlatformSensoryAuthority(profiles)
    session = authority.open(platform=MessagingPlatform.WHATSAPP, account_scope="primary",
        conversation_id="chat", channel_type=ChannelType.DM, members=(member,),
        privacy_scope=PrivacyScope.PRIVATE)
    with pytest.raises(ValueError, match="CONSENT_REQUIRED"):
        grant(authority, session, member, explicit_consent=False)
    with pytest.raises(ValueError, match="REMOTE_FORBIDDEN"):
        grant(authority, session, member, remote_processing_allowed=True)
    grant(authority, session, member, modalities=frozenset({SensoryModality.IMAGE}))
    context = authorize(authority, session, member, modality=SensoryModality.IMAGE)
    assert context.actor_id == "PERSON_BK" and context.source == ObservationSource.WHATSAPP
    assert context.audience == ("PERSON_BK",)
    assert not context.remote_processing_allowed
    authority.revoke(session.id, **args(session, member))
    with pytest.raises(ValueError, match="SESSION_UNAUTHORIZED"):
        authorize(authority, session, member, modality=SensoryModality.IMAGE)


def test_capacity_expiry_and_restart(setup):
    _, clock, profiles = setup
    authority = PlatformSensoryAuthority(profiles, max_sessions=1)
    session = open_group(authority, ttl=timedelta(seconds=1))
    grant(authority, session, BK)
    grant(authority, session, OTHER)
    with pytest.raises(ValueError, match="CAPACITY"):
        open_group(authority)
    with pytest.raises(ValueError, match="SESSION_UNAUTHORIZED"):
        authorize(PlatformSensoryAuthority(profiles), session, BK)
    clock.advance(timedelta(seconds=1))
    with pytest.raises(ValueError, match="SESSION_UNAUTHORIZED"):
        authorize(authority, session, BK)
    assert open_group(authority).id != session.id
