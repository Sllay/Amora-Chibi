"""Phase 2E.4: synthetic PG18 device/session/native-binding parity.

Directly mirrors the reviewed v18 AmoraDatabase SQL and identity models.
No DSN, no live Core wiring, no credential values logged. A caller supplies
disposable PostgreSQL 18 loopback amora_pg_synthetic, checked before SQL.
Authorization policy above the DB layer remains outside this experiment.
"""
from __future__ import annotations

import json
from datetime import datetime

from amora.identity.profiles import PlatformIdentityBinding
from amora.identity.session import (
    DeviceIdentity, DeviceTrust, PrivacyContext, SessionIdentity,
)

from .postgres_memory_lab import ensure_local_lab


class AuthLabError(ValueError):
    pass


def _iso(value: datetime) -> str:
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise AuthLabError("AUTH_LAB_AWARE_DATETIME_REQUIRED")
    return value.isoformat()


def upsert_device(conn, device: DeviceIdentity) -> None:
    ensure_local_lab(conn)
    if not isinstance(device, DeviceIdentity):
        raise AuthLabError("AUTH_LAB_INVALID_DEVICE")
    with conn.transaction():
        conn.execute("""
            INSERT INTO amora_pg_v18_lab.devices(
                id,owner_person_id,device_type,trust_level,credential_ref,
                capabilities_json,last_seen
            ) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(id) DO UPDATE SET
                owner_person_id=EXCLUDED.owner_person_id,
                device_type=EXCLUDED.device_type,
                trust_level=EXCLUDED.trust_level,
                credential_ref=EXCLUDED.credential_ref,
                capabilities_json=EXCLUDED.capabilities_json,
                last_seen=EXCLUDED.last_seen
        """, (device.id, device.owner_person_id, device.device_type,
              device.trust_level.value, device.credential_ref,
              json.dumps(sorted(device.capabilities)),
              _iso(device.last_seen)))


def get_device(conn, device_id: str) -> DeviceIdentity | None:
    ensure_local_lab(conn)
    row = conn.execute("""
        SELECT id,owner_person_id,device_type,trust_level,credential_ref,
               capabilities_json,last_seen
        FROM amora_pg_v18_lab.devices WHERE id=%s
    """, (device_id,)).fetchone()
    if row is None:
        return None
    return DeviceIdentity(
        id=row[0], owner_person_id=row[1], device_type=row[2],
        trust_level=DeviceTrust(row[3]), credential_ref=row[4],
        capabilities=set(json.loads(row[5])),
        last_seen=datetime.fromisoformat(row[6]),
    )


def save_session(conn, session: SessionIdentity) -> None:
    """Preserve canonical ON CONFLICT update field list exactly."""
    ensure_local_lab(conn)
    if not isinstance(session, SessionIdentity):
        raise AuthLabError("AUTH_LAB_INVALID_SESSION")
    with conn.transaction():
        conn.execute("""
            INSERT INTO amora_pg_v18_lab.sessions(
                id,device_id,authenticated_person_id,privacy_context,
                auth_method,started_at,last_seen,expires_at,capabilities_json,
                closed_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(id) DO UPDATE SET
                last_seen=EXCLUDED.last_seen,
                expires_at=EXCLUDED.expires_at,
                privacy_context=EXCLUDED.privacy_context,
                capabilities_json=EXCLUDED.capabilities_json,
                closed_at=EXCLUDED.closed_at
        """, (session.id, session.device_id,
              session.authenticated_person_id, session.privacy_context.value,
              session.auth_method, _iso(session.started_at),
              _iso(session.last_seen),
              _iso(session.expires_at) if session.expires_at else None,
              json.dumps(sorted(session.capabilities)),
              _iso(session.closed_at) if session.closed_at else None))


def get_session(conn, session_id: str) -> SessionIdentity | None:
    ensure_local_lab(conn)
    row = conn.execute("""
        SELECT id,device_id,authenticated_person_id,privacy_context,
               auth_method,started_at,last_seen,expires_at,capabilities_json,
               closed_at
        FROM amora_pg_v18_lab.sessions WHERE id=%s
    """, (session_id,)).fetchone()
    if row is None:
        return None
    return SessionIdentity(
        id=row[0], device_id=row[1],
        authenticated_person_id=row[2],
        privacy_context=PrivacyContext(row[3]), auth_method=row[4],
        started_at=datetime.fromisoformat(row[5]),
        last_seen=datetime.fromisoformat(row[6]),
        expires_at=datetime.fromisoformat(row[7]) if row[7] else None,
        capabilities=set(json.loads(row[8])),
        closed_at=datetime.fromisoformat(row[9]) if row[9] else None,
    )


def close_session(conn, session_id: str, closed_at: datetime) -> bool:
    ensure_local_lab(conn)
    stamp = _iso(closed_at)
    with conn.transaction():
        result = conn.execute("""
            UPDATE amora_pg_v18_lab.sessions
            SET closed_at=%s,last_seen=%s
            WHERE id=%s AND closed_at IS NULL
        """, (stamp, stamp, session_id))
        return result.rowcount > 0


def active_session_count(conn, now: datetime) -> int:
    ensure_local_lab(conn)
    row = conn.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.sessions
        WHERE closed_at IS NULL
          AND (expires_at IS NULL OR expires_at>%s)
    """, (_iso(now),)).fetchone()
    return int(row[0])


def upsert_native_binding(
    conn, binding: PlatformIdentityBinding, updated_at: datetime,
) -> None:
    """Native IDs validated by canonical Pydantic model; never aliases."""
    ensure_local_lab(conn)
    if not isinstance(binding, PlatformIdentityBinding):
        raise AuthLabError("AUTH_LAB_INVALID_BINDING")
    stamp = _iso(updated_at)
    with conn.transaction():
        conn.execute("""
            INSERT INTO amora_pg_v18_lab.platform_identity_bindings(
                platform,identifier_type,external_actor_id,account_scope,
                entity_id,trust,confidence,enabled,created_at,updated_at
            ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(platform,identifier_type,account_scope,external_actor_id)
            DO UPDATE SET
                entity_id=EXCLUDED.entity_id,
                trust=EXCLUDED.trust,
                confidence=EXCLUDED.confidence,
                enabled=EXCLUDED.enabled,
                updated_at=EXCLUDED.updated_at
        """, (binding.platform.value,binding.identifier_type,
              binding.external_actor_id,binding.account_scope,
              binding.entity_id,binding.trust.value,binding.confidence,
              int(binding.enabled),stamp,stamp))


def resolve_native_binding(
    conn, *, platform: str, identifier_type: str,
    external_actor_id: str, account_scope: str,
) -> str | None:
    ensure_local_lab(conn)
    row = conn.execute("""
        SELECT entity_id FROM amora_pg_v18_lab.platform_identity_bindings
        WHERE platform=%s AND identifier_type=%s AND external_actor_id=%s
          AND account_scope=%s AND enabled=1
        ORDER BY confidence DESC,updated_at DESC LIMIT 1
    """, (platform,identifier_type,external_actor_id,account_scope)).fetchone()
    return str(row[0]) if row else None


def has_verified_binding(
    conn, *, platform: str, identifier_type: str,
    external_actor_id: str, account_scope: str, entity_id: str,
) -> bool:
    """Match canonical exact native key plus explicit trust level only."""
    ensure_local_lab(conn)
    row = conn.execute("""
        SELECT platform,identifier_type,external_actor_id,account_scope,
               entity_id,trust,confidence,enabled
        FROM amora_pg_v18_lab.platform_identity_bindings
        WHERE platform=%s AND identifier_type=%s AND external_actor_id=%s
          AND account_scope=%s AND entity_id=%s AND enabled=1
          AND trust IN ('operator_verified','platform_authenticated')
    """, (platform,identifier_type,external_actor_id,
          account_scope,entity_id)).fetchone()
    if row is None:
        return False
    from pydantic import ValidationError
    try:
        parsed = PlatformIdentityBinding.model_validate({
            "platform":row[0],"identifier_type":row[1],
            "external_actor_id":row[2],"account_scope":row[3],
            "entity_id":row[4],"trust":row[5],"confidence":row[6],
            "enabled":bool(row[7]),
        })
    except ValidationError:
        return False
    return parsed.enabled
