"""Reviewed PostgreSQL v18 SQL for AmoraDatabase's event/identity/session paths.

Keys are exact canonical AmoraDatabase SQL after whitespace normalization.
No production schema selection; only the synthetic v18 namespace is supported.
The caller must still ensure principal authorization above this store.
"""
from __future__ import annotations

from .postgres_runtime_connection import PostgresRuntimeError, ReviewedStatement


def identity_statements(*, schema: str = "amora_pg_v18_lab") -> dict[str, ReviewedStatement]:
    if schema != "amora_pg_v18_lab":
        raise PostgresRuntimeError("PG_IDENTITY_SCHEMA_NOT_REVIEWED")
    m: dict[str, ReviewedStatement] = {}
    def add(sql: str, pg: str, count: int, max_rows: int = 1000) -> None:
        key = " ".join(sql.split())
        if key in m:
            raise PostgresRuntimeError("PG_IDENTITY_DUPLICATE_SQL")
        m[key] = ReviewedStatement(pg, count, max_rows)

    add("""INSERT INTO events (id, schema_version, event_type, timestamp, source, actor, target, confidence, importance, priority, payload_json, privacy_scope, session_id, correlation_id, causation_id, causal_parent, sequence, dedupe_key) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(dedupe_key) DO NOTHING""",
        """INSERT INTO amora_pg_v18_lab.events (
            id,schema_version,event_type,timestamp,source,actor,target,confidence,
            importance,priority,payload_json,privacy_scope,session_id,
            correlation_id,causation_id,causal_parent,sequence,dedupe_key)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(dedupe_key) DO NOTHING""", 18)
    add("SELECT event_type FROM events WHERE dedupe_key = ?",
        "SELECT event_type FROM amora_pg_v18_lab.events WHERE dedupe_key=%s", 1, 1)
    add("SELECT * FROM events WHERE dedupe_key = ?",
        "SELECT * FROM amora_pg_v18_lab.events WHERE dedupe_key=%s", 1, 1)
    add("SELECT * FROM events WHERE id=? AND event_type=?",
        "SELECT * FROM amora_pg_v18_lab.events WHERE id=%s AND event_type=%s", 2, 1)
    add("SELECT COUNT(*) FROM events",
        "SELECT COUNT(*) FROM amora_pg_v18_lab.events", 0, 1)

    add("INSERT INTO entities(id, canonical_name, entity_type, payload_json) VALUES (?, ?, ?, '{}') ON CONFLICT(id) DO UPDATE SET canonical_name=excluded.canonical_name, entity_type=excluded.entity_type",
        """INSERT INTO amora_pg_v18_lab.entities(
            id,canonical_name,entity_type,payload_json
            ) VALUES (%s,%s,%s,'{}')
            ON CONFLICT(id) DO UPDATE SET canonical_name=EXCLUDED.canonical_name,
            entity_type=EXCLUDED.entity_type""", 3)
    add("DELETE FROM aliases WHERE entity_id = ?",
        "DELETE FROM amora_pg_v18_lab.aliases WHERE entity_id=%s", 1)
    add("INSERT INTO aliases(normalized_alias, entity_id) VALUES (?, ?)",
        "INSERT INTO amora_pg_v18_lab.aliases(normalized_alias,entity_id) VALUES (%s,%s)", 2)
    add("INSERT INTO relationships(source, relation_type, target, confidence) VALUES (?, ?, ?, ?) ON CONFLICT(source, relation_type, target) DO UPDATE SET confidence=excluded.confidence",
        """INSERT INTO amora_pg_v18_lab.relationships(
            source,relation_type,target,confidence) VALUES (%s,%s,%s,%s)
            ON CONFLICT(source,relation_type,target) DO UPDATE
            SET confidence=EXCLUDED.confidence""", 4)
    add("SELECT id, canonical_name, entity_type FROM entities ORDER BY id",
        "SELECT id,canonical_name,entity_type FROM amora_pg_v18_lab.entities ORDER BY id", 0, 10000)
    add("SELECT normalized_alias, entity_id FROM aliases",
        "SELECT normalized_alias,entity_id FROM amora_pg_v18_lab.aliases", 0, 50000)
    add("SELECT source, relation_type, target, confidence FROM relationships ORDER BY id",
        "SELECT source,relation_type,target,confidence FROM amora_pg_v18_lab.relationships ORDER BY id", 0, 50000)

    add("""INSERT INTO person_profiles(entity_id, profile_json, source_revision, updated_at)
                   VALUES (?, ?, ?, ?)
                   ON CONFLICT(entity_id) DO UPDATE SET
                     profile_json=excluded.profile_json,
                     source_revision=excluded.source_revision,
                     updated_at=excluded.updated_at""",
        """INSERT INTO amora_pg_v18_lab.person_profiles(
            entity_id,profile_json,source_revision,updated_at)
            VALUES (%s,%s,%s,%s) ON CONFLICT(entity_id) DO UPDATE SET
            profile_json=EXCLUDED.profile_json,
            source_revision=EXCLUDED.source_revision,
            updated_at=EXCLUDED.updated_at""",4)
    add("SELECT profile_json FROM person_profiles WHERE entity_id = ?",
        "SELECT profile_json FROM amora_pg_v18_lab.person_profiles WHERE entity_id=%s",1,1)
    add("SELECT profile_json FROM person_profiles ORDER BY entity_id",
        "SELECT profile_json FROM amora_pg_v18_lab.person_profiles ORDER BY entity_id",0,10000)

    add("""INSERT INTO platform_identity_bindings(
                       platform, identifier_type, external_actor_id, account_scope,
                       entity_id, trust, confidence, enabled, created_at, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(platform, identifier_type, account_scope, external_actor_id) DO UPDATE SET
                       entity_id=excluded.entity_id,
                       trust=excluded.trust,
                       confidence=excluded.confidence,
                       enabled=excluded.enabled,
                       updated_at=excluded.updated_at""",
        """INSERT INTO amora_pg_v18_lab.platform_identity_bindings(
            platform,identifier_type,external_actor_id,account_scope,
            entity_id,trust,confidence,enabled,created_at,updated_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(platform,identifier_type,account_scope,external_actor_id)
            DO UPDATE SET entity_id=EXCLUDED.entity_id,trust=EXCLUDED.trust,
            confidence=EXCLUDED.confidence,enabled=EXCLUDED.enabled,
            updated_at=EXCLUDED.updated_at""",10)
    add("""SELECT entity_id FROM platform_identity_bindings
                   WHERE platform = ? AND identifier_type = ? AND external_actor_id = ?
                     AND account_scope = ? AND enabled = 1
                   ORDER BY confidence DESC, updated_at DESC LIMIT 1""",
        """SELECT entity_id FROM amora_pg_v18_lab.platform_identity_bindings
            WHERE platform=%s AND identifier_type=%s AND external_actor_id=%s
              AND account_scope=%s AND enabled=1
            ORDER BY confidence DESC,updated_at DESC LIMIT 1""",4,1)
    add("""SELECT platform, identifier_type, external_actor_id, account_scope,
                          entity_id, trust, confidence, enabled
                   FROM platform_identity_bindings
                   WHERE platform=? AND identifier_type=? AND external_actor_id=?
                     AND account_scope=? AND entity_id=? AND enabled=1
                     AND trust IN ('operator_verified','platform_authenticated')""",
        """SELECT platform,identifier_type,external_actor_id,account_scope,
            entity_id,trust,confidence,enabled
            FROM amora_pg_v18_lab.platform_identity_bindings
            WHERE platform=%s AND identifier_type=%s AND external_actor_id=%s
            AND account_scope=%s AND entity_id=%s AND enabled=1
            AND trust IN ('operator_verified','platform_authenticated')""",5,1)
    add("""SELECT platform, identifier_type, external_actor_id, account_scope,
                          entity_id, trust, confidence, enabled
                   FROM platform_identity_bindings
                   WHERE entity_id = ? ORDER BY platform, identifier_type, id""",
        """SELECT platform,identifier_type,external_actor_id,account_scope,
            entity_id,trust,confidence,enabled
            FROM amora_pg_v18_lab.platform_identity_bindings
            WHERE entity_id=%s ORDER BY platform,identifier_type,id""",1,10000)

    add("""INSERT INTO devices(id, owner_person_id, device_type, trust_level, credential_ref, capabilities_json, last_seen) VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET owner_person_id=excluded.owner_person_id, device_type=excluded.device_type, trust_level=excluded.trust_level, credential_ref=excluded.credential_ref, capabilities_json=excluded.capabilities_json, last_seen=excluded.last_seen""",
        """INSERT INTO amora_pg_v18_lab.devices(
            id,owner_person_id,device_type,trust_level,credential_ref,
            capabilities_json,last_seen) VALUES (%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(id) DO UPDATE SET
            owner_person_id=EXCLUDED.owner_person_id,
            device_type=EXCLUDED.device_type,trust_level=EXCLUDED.trust_level,
            credential_ref=EXCLUDED.credential_ref,
            capabilities_json=EXCLUDED.capabilities_json,
            last_seen=EXCLUDED.last_seen""",7)
    add("SELECT * FROM devices WHERE id = ?",
        "SELECT * FROM amora_pg_v18_lab.devices WHERE id=%s",1,1)
    add("""INSERT INTO sessions(id, device_id, authenticated_person_id, privacy_context, auth_method, started_at, last_seen, expires_at, capabilities_json, closed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET last_seen=excluded.last_seen, expires_at=excluded.expires_at, privacy_context=excluded.privacy_context, capabilities_json=excluded.capabilities_json, closed_at=excluded.closed_at""",
        """INSERT INTO amora_pg_v18_lab.sessions(
            id,device_id,authenticated_person_id,privacy_context,auth_method,
            started_at,last_seen,expires_at,capabilities_json,closed_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT(id) DO UPDATE SET
            last_seen=EXCLUDED.last_seen,expires_at=EXCLUDED.expires_at,
            privacy_context=EXCLUDED.privacy_context,
            capabilities_json=EXCLUDED.capabilities_json,
            closed_at=EXCLUDED.closed_at""",10)
    add("SELECT * FROM sessions WHERE id = ?",
        "SELECT * FROM amora_pg_v18_lab.sessions WHERE id=%s",1,1)
    add("UPDATE sessions SET closed_at = ?, last_seen = ? WHERE id = ? AND closed_at IS NULL",
        "UPDATE amora_pg_v18_lab.sessions SET closed_at=%s,last_seen=%s WHERE id=%s AND closed_at IS NULL",3)
    add("SELECT COUNT(*) FROM sessions WHERE closed_at IS NULL AND (expires_at IS NULL OR expires_at > ?)",
        "SELECT COUNT(*) FROM amora_pg_v18_lab.sessions WHERE closed_at IS NULL AND (expires_at IS NULL OR expires_at>%s)",1,1)
    return m
