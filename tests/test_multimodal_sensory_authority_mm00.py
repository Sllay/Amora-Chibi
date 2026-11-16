"""MM.00D.3A.1: real session/device store, explicit bounded sensory grants."""
from datetime import timedelta

import pytest

from amora.events import PrivacyScope
from amora.events.multimodal import ObservationSource, SensoryModality
from amora.events.sensory_authority import DeviceSensoryAuthority
from amora.identity.models import build_initial_registry
from amora.identity.session import DeviceIdentity, DeviceTrust, PrivacyContext, SessionService
from amora.storage import AmoraDatabase
from amora.time import SimulatedClock
from test_multimodal_contract_mm00 import NOW


@pytest.fixture
def setup(tmp_path):
    db = AmoraDatabase(tmp_path / "authority.db")
    db.migrate()
    db.save_registry(build_initial_registry())
    clock = SimulatedClock(NOW)
    sessions = SessionService(db, clock)
    device = DeviceIdentity(id="DEVICE_A15", owner_person_id="PERSON_BK",
                            device_type="phone", trust_level=DeviceTrust.VERIFIED, last_seen=NOW)
    sessions.register_device(device)
    session = sessions.start_local_session(device.id, privacy_context=PrivacyContext.TRUSTED)
    try:
        yield db, clock, sessions, device, session
    finally:
        db.close()


def issue(authority, session, **overrides):
    return authority.issue(**{
        "session_id": session.id, "authenticated_person_id": "PERSON_BK",
        "modalities": frozenset({SensoryModality.SPEECH, SensoryModality.SCREEN}),
        "explicit_consent": True, **overrides,
    })


def check(authority, grant, **overrides):
    return authority.authorize(grant.grant_id, **{
        "authenticated_person_id": "PERSON_BK", "modality": SensoryModality.SPEECH, **overrides,
    })


def test_explicit_grant_shared_correlation_and_isolated_streams(setup):
    db, clock, sessions, _, session = setup
    authority = DeviceSensoryAuthority(sessions)
    grant = issue(authority, session, remote_processing_allowed=True)
    speech = check(authority, grant, for_remote_processing=True)
    screen = check(authority, grant, modality=SensoryModality.SCREEN, for_remote_processing=True)
    assert speech.actor_id == "PERSON_BK"
    assert screen.actor_id is None
    assert speech.source == ObservationSource.WEB
    assert screen.source == ObservationSource.SCREEN
    assert speech.stream_id != screen.stream_id
    assert speech.correlation_id == screen.correlation_id
    assert speech.scope_id == screen.scope_id
    assert speech.audience == screen.audience == ("PERSON_BK",)
    assert speech.privacy_scope == PrivacyScope.PERSONAL
    assert speech.remote_processing_allowed
    assert check(authority, grant) == speech
    with pytest.raises(ValueError, match="SENSORY_MODALITY_FORBIDDEN"):
        check(authority, grant, modality=SensoryModality.IMAGE)
    another = issue(authority, session)
    assert check(authority, another).stream_id != speech.stream_id
    assert another.correlation_id != grant.correlation_id
    assert db.event_count() == 0


@pytest.mark.parametrize("override,reason", [
    ({"explicit_consent": False}, "SENSORY_CONSENT_REQUIRED"),
    ({"explicit_consent": 1}, "SENSORY_CONSENT_REQUIRED"),
    ({"modalities": frozenset()}, "SENSORY_MODALITY_INVALID"),
    ({"modalities": frozenset({"speech"})}, "SENSORY_MODALITY_INVALID"),
    ({"remote_processing_allowed": "true"}, "SENSORY_POLICY_INVALID"),
    ({"authenticated_person_id": "PERSON_OTHER"}, "SENSORY_SESSION_UNAUTHORIZED"),
    ({"session_id": "SESSION_MISSING"}, "SENSORY_SESSION_UNAUTHORIZED"),
    ({"ttl": timedelta(hours=3)}, "SENSORY_GRANT_TTL_INVALID"),
])
def test_invalid_consent_and_identity_fail_closed(setup, override, reason):
    _, _, sessions, _, session = setup
    with pytest.raises(ValueError, match=reason):
        issue(DeviceSensoryAuthority(sessions), session, **override)


def test_private_session_never_permits_remote_and_public_is_rejected(setup):
    _, _, sessions, device, _ = setup
    private = sessions.start_local_session(device.id, privacy_context=PrivacyContext.PRIVATE)
    authority = DeviceSensoryAuthority(sessions)
    grant = issue(authority, private)
    assert check(authority, grant).privacy_scope == PrivacyScope.PRIVATE
    assert not check(authority, grant).remote_processing_allowed
    with pytest.raises(ValueError, match="SENSORY_REMOTE_FORBIDDEN"):
        issue(authority, private, remote_processing_allowed=True)
    with pytest.raises(ValueError, match="SENSORY_REMOTE_FORBIDDEN"):
        check(authority, grant, for_remote_processing=True)
    public = sessions.start_local_session(device.id, privacy_context=PrivacyContext.PUBLIC)
    with pytest.raises(ValueError, match="SENSORY_SESSION_UNAUTHORIZED"):
        issue(authority, public)


def test_revocation_expiry_capacity_and_restart(setup):
    _, clock, sessions, device, session = setup
    authority = DeviceSensoryAuthority(sessions, max_grants=1)
    grant = issue(authority, session, ttl=timedelta(seconds=10))
    with pytest.raises(ValueError, match="SENSORY_GRANT_CAPACITY"):
        issue(authority, session)
    with pytest.raises(ValueError, match="SENSORY_GRANT_UNAUTHORIZED"):
        check(authority, grant, authenticated_person_id="PERSON_OTHER")
    assert not authority.revoke(grant.grant_id, authenticated_person_id="PERSON_OTHER")
    assert check(authority, grant).actor_id == "PERSON_BK"
    assert authority.revoke(grant.grant_id, authenticated_person_id="PERSON_BK")
    with pytest.raises(ValueError, match="SENSORY_GRANT_UNAUTHORIZED"):
        check(authority, grant)
    grant = issue(authority, session, ttl=timedelta(seconds=10))
    clock.advance(timedelta(seconds=10))
    with pytest.raises(ValueError, match="SENSORY_GRANT_UNAUTHORIZED"):
        check(authority, grant)
    short = sessions.start_local_session(device.id, ttl=timedelta(seconds=5),
                                         privacy_context=PrivacyContext.TRUSTED)
    grant = issue(authority, short)
    assert grant.expires_at == short.expires_at
    with pytest.raises(ValueError, match="SENSORY_GRANT_UNAUTHORIZED"):
        check(DeviceSensoryAuthority(sessions), grant)
    clock.advance(timedelta(seconds=5))
    with pytest.raises(ValueError, match="SENSORY_GRANT_UNAUTHORIZED"):
        check(authority, grant)


@pytest.mark.parametrize("change,reason", [
    ("closed", "SENSORY_SESSION_UNAUTHORIZED"),
    ("scope", "SENSORY_SESSION_CHANGED"),
    ("trust", "SENSORY_DEVICE_UNAUTHORIZED"),
    ("owner", "SENSORY_DEVICE_UNAUTHORIZED"),
])
def test_session_and_device_revalidation_permanently_invalidates_grant(setup, change, reason):
    db, _, sessions, device, session = setup
    authority = DeviceSensoryAuthority(sessions)
    grant = issue(authority, session)
    if change == "closed":
        sessions.close(session.id)
    elif change == "scope":
        db.save_session(session.model_copy(update={"privacy_context": PrivacyContext.PRIVATE}))
    elif change == "trust":
        sessions.register_device(device.model_copy(update={"trust_level": DeviceTrust.UNKNOWN}))
    else:
        sessions.register_device(device.model_copy(update={"owner_person_id": None}))
    with pytest.raises(ValueError, match=reason):
        check(authority, grant)
    # Restoring authority cannot resurrect a grant already invalidated.
    db.save_session(session)
    sessions.register_device(device)
    with pytest.raises(ValueError, match="SENSORY_GRANT_UNAUTHORIZED"):
        check(authority, grant)


def test_untrusted_device_session_never_grants_capture(setup):
    _, _, sessions, device, _ = setup
    sessions.register_device(device.model_copy(update={"trust_level": DeviceTrust.UNKNOWN}))
    session = sessions.start_local_session(device.id, privacy_context=PrivacyContext.TRUSTED)
    assert session.authenticated_person_id is None
    with pytest.raises(ValueError, match="SENSORY_SESSION_UNAUTHORIZED"):
        issue(DeviceSensoryAuthority(sessions), session)
