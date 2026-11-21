"""Offline contracts for reversible hosting profiles and PG17 read projection."""
from __future__ import annotations

import sys
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "core"))

from amora.deployment_profiles import (
    ACTIVE_DEPLOYMENT_PROFILE, DeploymentProfile, MemoryRoles, get_profile, release_plan,
)
from amora.events import PrivacyScope
from amora.memory.models import (
    MemoryKind, MemoryProvenance, MemoryRecord, ProvenanceKind,
)
from amora.memory.readmodel_projection import (
    MemoryReadProjection, can_serve_projection, project_memory,
)


def now():
    return datetime(2026, 11, 20, 18, 0, tzinfo=timezone.utc)


def record(*, owner="PERSON_BK", scope=PrivacyScope.PERSONAL,
           remote=True, archived=False):
    t = now() - timedelta(seconds=5)
    return MemoryRecord(
        id=uuid4(), kind=MemoryKind.EPISODIC,
        content="Owner and assistant discussed the Godot Engine.",
        summary="Godot Engine discussion",
        provenance=MemoryProvenance(kind=ProvenanceKind.USER_ASSERTION,
                                    source="owner_discord", confidence=1.0),
        privacy_scope=scope, owner_entity_id=owner,
        remote_processing_allowed=remote, created_at=t, updated_at=t,
        archived_at=t if archived else None,
        importance=0.7, gate_score=0.9,
    )


def test_host_selection_is_internal_and_explicit_not_inferred_from_environment(monkeypatch):
    monkeypatch.setenv("AMORA_DEPLOYMENT_PROFILE", "altare")
    assert ACTIVE_DEPLOYMENT_PROFILE == "northflank"
    assert get_profile().host == "northflank"
    assert get_profile("altare").host == "altare"
    assert get_profile("northflank").memory.authoritative == "aiven_pg18"
    assert get_profile("altare").memory.authoritative == "aiven_pg18"
    assert get_profile("altare").memory.cache_mode == "shadow"
    assert get_profile("altare").memory.cache_pg_major == 17
    assert "dsn" not in str(release_plan()).lower()
    assert "password" not in str(release_plan()).lower()


def test_invalid_authority_modes_or_host_combinations_fail_closed():
    with pytest.raises(ValueError, match="UNKNOWN_DEPLOYMENT_PROFILE"):
        get_profile("unlisted-provider")
    with pytest.raises(ValueError, match="AUTHORITY_MUST"):
        MemoryRoles(authoritative="altare_pg17", primary_pg_major=17)
    with pytest.raises(ValueError, match="CACHE_MODE"):
        MemoryRoles(cache="altare_pg17", cache_mode="off", cache_pg_major=17)
    with pytest.raises(ValueError, match="READMODEL_MUST"):
        DeploymentProfile(
            name="invalid", host="northflank",
            core_service="a", discord_service="b", web_service="c",
            memory=MemoryRoles(cache="altare_pg17", cache_mode="shadow",
                               cache_pg_major=17),
        )
    with pytest.raises(FrozenInstanceError):
        get_profile("altare").host = "northflank"


def test_pg17_projection_retains_identity_and_approved_provenance():
    m = record()
    projection = project_memory(m, origin_sequence=123, materialized_at=now())
    assert projection is not None
    assert projection.memory_id == str(m.id)
    assert projection.owner_entity_id == "PERSON_BK"
    assert projection.origin_sequence == 123
    assert projection.schema == 1
    assert can_serve_projection(projection, requester_owner_id="PERSON_BK",
                                required_sequence=123, now=now())


@pytest.mark.parametrize("kwargs", [
    {"owner": None}, {"scope": PrivacyScope.SECRET},
    {"scope": PrivacyScope.AUTH}, {"remote": False},
])
def test_private_or_nonconsenting_memories_never_enter_projection(kwargs):
    assert project_memory(record(**kwargs), origin_sequence=3, materialized_at=now()) is None


def test_cache_never_crosses_owner_or_outlives_canonical_revision():
    p = project_memory(record(), origin_sequence=9, materialized_at=now())
    assert p is not None
    check = lambda **kwargs: can_serve_projection(
        p, requester_owner_id="PERSON_BK", required_sequence=9,
        now=now(), **kwargs)
    assert check()
    assert not can_serve_projection(p, requester_owner_id="OTHER_PERSON",
                                    required_sequence=9, now=now())
    assert not can_serve_projection(p, requester_owner_id="PERSON_BK",
                                    required_sequence=10, now=now())
    assert not can_serve_projection(p, requester_owner_id="PERSON_BK",
                                    required_sequence=0, now=now())
    assert not can_serve_projection(p, requester_owner_id="PERSON_BK",
                                    required_sequence=9,
                                    now=now() + timedelta(seconds=31))
    assert not check(max_staleness=timedelta(minutes=15))
    tampered = replace(p, summary="Injected content", checksum=p.checksum)
    assert not can_serve_projection(tampered, requester_owner_id="PERSON_BK",
                                    required_sequence=9, now=now())
    forged_revision = replace(p, origin_sequence=10)
    assert not can_serve_projection(forged_revision,
                                    requester_owner_id="PERSON_BK",
                                    required_sequence=9, now=now())
    malformed_timestamp = replace(p, origin_updated_at=datetime(2026, 11, 20))
    assert not can_serve_projection(malformed_timestamp,
                                    requester_owner_id="PERSON_BK",
                                    required_sequence=9, now=now())


def test_archive_and_expiry_projections_cannot_be_served():
    deleted = project_memory(record(archived=True), origin_sequence=12,
                             materialized_at=now())
    assert deleted and deleted.summary == "" and deleted.archived
    assert not can_serve_projection(deleted, requester_owner_id="PERSON_BK",
                                    required_sequence=12, now=now())
    existing = project_memory(record(), origin_sequence=10, materialized_at=now())
    expired = replace(existing, valid_until=now() - timedelta(seconds=1))
    assert not can_serve_projection(expired, requester_owner_id="PERSON_BK",
                                    required_sequence=10, now=now())


def test_projection_requires_real_committed_sequence_and_timezone():
    m = record()
    with pytest.raises(ValueError, match="SEQUENCE"):
        project_memory(m, origin_sequence=0, materialized_at=now())
    with pytest.raises(ValueError, match="TZ_AWARE"):
        project_memory(m, origin_sequence=5, materialized_at=datetime(2026, 11, 20))
