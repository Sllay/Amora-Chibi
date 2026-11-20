"""Canonical AmoraDatabase v18 versus PG18 auth/session identity parity.

In-memory SQLite actual AmoraDatabase methods; disposable PostgreSQL 18 lab.
Only invented IDs and capability strings. No keys or private records.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from threading import RLock
from types import SimpleNamespace

import os
import sqlite3
import pytest

from amora.identity.profiles import (
    PlatformIdentityBinding, PlatformKind, BindingTrust,
)
from amora.identity.session import (
    DeviceIdentity, DeviceTrust, PrivacyContext, SessionIdentity,
)
from amora.storage.database import AmoraDatabase
from amora.storage.backend import SQLiteBackend
from amora.storage import postgres_auth_sessions_lab as auth
from amora.storage import postgres_v18_manifest as v18
from amora.storage import postgres_v18_lab_schema as schema
from amora.storage.postgres_memory_lab import ensure_local_lab

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Disposable PostgreSQL 18 loopback CI only",
)
NOW = datetime(2026, 11, 17, 12, tzinfo=timezone.utc)


@pytest.fixture
def pair():
    psycopg = pytest.importorskip("psycopg")
    pg = psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )
    sq = v18.synthetic_v18_connection()
    sq.row_factory = sqlite3.Row
    db = AmoraDatabase.__new__(AmoraDatabase)
    db._conn = sq
    db._lock = RLock()
    db.backend = SQLiteBackend()
    try:
        ensure_local_lab(pg)
        pg.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        schema.install_pg_v18_lab(pg)
        for identity in ("PERSON_ALPHA", "PERSON_BETA"):
            sq.execute("""
                INSERT INTO entities(id,canonical_name,entity_type,payload_json)
                VALUES (?,?, 'person','{}')
            """, (identity, identity))
            pg.execute("""
                INSERT INTO amora_pg_v18_lab.entities(
                    id,canonical_name,entity_type,payload_json
                ) VALUES (%s,%s,'person','{}')
            """, (identity, identity))
        sq.commit()
        yield db, pg
    finally:
        sq.close()
        pg.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        pg.close()


def device(*, trust=DeviceTrust.VERIFIED, last_seen=NOW):
    return DeviceIdentity(
        id="synthetic-device-1", owner_person_id="PERSON_ALPHA",
        device_type="mobile", trust_level=trust,
        credential_ref="synthetic-ref-not-a-secret",
        capabilities={"tts", "chat"}, last_seen=last_seen,
    )


def session(*, last_seen=NOW, expiration=None,
            context=PrivacyContext.PRIVATE, closed=None):
    return SessionIdentity(
        id="synthetic-session-1", device_id="synthetic-device-1",
        authenticated_person_id="PERSON_ALPHA",
        privacy_context=context, auth_method="local_api_token",
        started_at=NOW, last_seen=last_seen,
        expires_at=expiration,
        capabilities={"tts", "chat"}, closed_at=closed,
    )


def assert_same(a, b):
    assert (a is None) == (b is None)
    if a is not None:
        assert a.model_dump(mode="json") == b.model_dump(mode="json")


def test_device_session_persistence_and_restart_parity(pair):
    db, pg = pair
    assert db.get_device("synthetic-device-1") is None
    assert auth.get_device(pg, "synthetic-device-1") is None
    first = device()
    db.upsert_device(first)
    auth.upsert_device(pg, first)
    assert_same(db.get_device(first.id), auth.get_device(pg, first.id))
    later = device(trust=DeviceTrust.LOCAL_TRUSTED, last_seen=NOW+timedelta(minutes=3))
    db.upsert_device(later)
    auth.upsert_device(pg, later)
    assert_same(db.get_device(first.id), auth.get_device(pg, first.id))
    current = session(expiration=NOW+timedelta(hours=1))
    db.save_session(current)
    auth.save_session(pg, current)
    assert_same(db.get_session(current.id), auth.get_session(pg, current.id))
    assert db.active_session_count(NOW) == auth.active_session_count(pg, NOW) == 1
    update = session(
        last_seen=NOW+timedelta(minutes=5),
        expiration=NOW+timedelta(hours=2),
        context=PrivacyContext.TRUSTED,
    )
    db.save_session(update)
    auth.save_session(pg, update)
    assert_same(db.get_session(update.id), auth.get_session(pg, update.id))
    assert db.active_session_count(NOW+timedelta(hours=1)) == auth.active_session_count(
        pg, NOW+timedelta(hours=1)
    ) == 1
    assert db.close_session(update.id, NOW+timedelta(minutes=6)) is True
    assert auth.close_session(pg, update.id, NOW+timedelta(minutes=6)) is True
    assert db.close_session(update.id, NOW+timedelta(minutes=7)) is False
    assert auth.close_session(pg, update.id, NOW+timedelta(minutes=7)) is False
    assert_same(db.get_session(update.id), auth.get_session(pg, update.id))
    assert db.active_session_count(NOW) == auth.active_session_count(pg, NOW) == 0


def test_expired_session_and_foreign_key_guard(pair):
    db, pg = pair
    item = device()
    db.upsert_device(item)
    auth.upsert_device(pg, item)
    stale = session(expiration=NOW-timedelta(seconds=1))
    db.save_session(stale)
    auth.save_session(pg, stale)
    assert db.active_session_count(NOW) == auth.active_session_count(pg, NOW) == 0
    psycopg = pytest.importorskip("psycopg")
    bad = SessionIdentity(
        id="synthetic-bad-session",device_id="unknown-device",
        started_at=NOW,last_seen=NOW,
    )
    with pytest.raises(sqlite3.IntegrityError):
        db.save_session(bad)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        auth.save_session(pg, bad)
    assert db.get_session(bad.id) is None
    assert auth.get_session(pg, bad.id) is None


def binding(*, trust=BindingTrust.OPERATOR_VERIFIED, enabled=True,
            account_scope="primary", entity="PERSON_ALPHA"):
    return PlatformIdentityBinding(
        platform=PlatformKind.DISCORD,
        identifier_type="discord_user_id",
        external_actor_id="123456789012345678",
        account_scope=account_scope,
        entity_id=entity,
        trust=trust, enabled=enabled, confidence=0.95,
    )


def resolve_pg(pg, entry):
    return auth.resolve_native_binding(
        pg, platform=entry.platform.value, identifier_type=entry.identifier_type,
        external_actor_id=entry.external_actor_id,
        account_scope=entry.account_scope,
    )


def resolve_sqlite(db, entry):
    return db.resolve_platform_identity_binding(
        platform=entry.platform.value, identifier_type=entry.identifier_type,
        external_actor_id=entry.external_actor_id,
        account_scope=entry.account_scope,
    )


def verified_pg(pg, entry):
    return auth.has_verified_binding(
        pg,platform=entry.platform.value,identifier_type=entry.identifier_type,
        external_actor_id=entry.external_actor_id,
        account_scope=entry.account_scope,entity_id=entry.entity_id,
    )


def verified_sqlite(db, entry):
    return db.has_verified_platform_binding(
        platform=entry.platform.value, identifier_type=entry.identifier_type,
        external_actor_id=entry.external_actor_id,
        account_scope=entry.account_scope,entity_id=entry.entity_id,
    )


def test_discord_exact_native_binding_trust_and_scope(pair):
    db, pg = pair
    item = binding()
    assert resolve_sqlite(db, item) is None
    assert resolve_pg(pg, item) is None
    db.upsert_platform_identity_binding(item, updated_at=NOW)
    auth.upsert_native_binding(pg, item, NOW)
    assert resolve_sqlite(db, item) == resolve_pg(pg, item) == "PERSON_ALPHA"
    assert verified_sqlite(db, item) == verified_pg(pg, item) is True
    assert verified_sqlite(db, binding(entity="PERSON_BETA")) is False
    assert verified_pg(pg, binding(entity="PERSON_BETA")) is False
    # An observed native key can be resolved internally, but may not grant
    # operator_verified / platform_authenticated capabilities.
    observed = binding(trust=BindingTrust.OBSERVED)
    db.upsert_platform_identity_binding(observed, updated_at=NOW+timedelta(minutes=1))
    auth.upsert_native_binding(pg, observed, NOW+timedelta(minutes=1))
    assert resolve_sqlite(db, observed) == resolve_pg(pg, observed) == "PERSON_ALPHA"
    assert verified_sqlite(db, observed) == verified_pg(pg, observed) is False
    secondary = binding(account_scope="secondary")
    db.upsert_platform_identity_binding(secondary, updated_at=NOW)
    auth.upsert_native_binding(pg, secondary, NOW)
    assert resolve_sqlite(db, secondary) == resolve_pg(pg, secondary) == "PERSON_ALPHA"
    disabled = binding(enabled=False)
    db.upsert_platform_identity_binding(disabled, updated_at=NOW+timedelta(minutes=2))
    auth.upsert_native_binding(pg, disabled, NOW+timedelta(minutes=2))
    assert resolve_sqlite(db, disabled) is None
    assert resolve_pg(pg, disabled) is None
    assert verified_sqlite(db, disabled) == verified_pg(pg, disabled) is False
    assert resolve_sqlite(db, secondary) == resolve_pg(pg, secondary) == "PERSON_ALPHA"


def test_binding_fk_failure_and_username_impersonation_rejected(pair):
    db, pg = pair
    psycopg = pytest.importorskip("psycopg")
    invalid_owner = binding(entity="PERSON_MISSING")
    with pytest.raises(sqlite3.IntegrityError):
        db.upsert_platform_identity_binding(invalid_owner, updated_at=NOW)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        auth.upsert_native_binding(pg, invalid_owner, NOW)
    assert resolve_sqlite(db, invalid_owner) == resolve_pg(pg, invalid_owner) is None
    from pydantic import ValidationError
    with pytest.raises(ValidationError):
        PlatformIdentityBinding(
            platform="discord", identifier_type="discord_user_id",
            external_actor_id="mutable-username",
            entity_id="PERSON_ALPHA",
        )


def test_no_external_pg_sql():
    class Remote:
        info = SimpleNamespace(
            host="service.aivencloud.com",
            dbname="amora_pg_synthetic",
            server_version=180000,
        )
        def execute(self, *_a, **_kw):
            raise AssertionError("No remote SQL permitted")
        def transaction(self):
            raise AssertionError("No remote transactions permitted")
    with pytest.raises(ValueError, match="POSTGRES_LAB_LOCAL_DB_REQUIRED"):
        auth.active_session_count(Remote(), NOW)
