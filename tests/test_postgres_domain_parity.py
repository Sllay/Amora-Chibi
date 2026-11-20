"""SQLite v18 vs PostgreSQL 18 identity and conversation domain parity.

Only invented local fixtures. No Aiven credentials, no private snapshot,
and no production Core connection. This does NOT certify all Discord
messaging/voice/identity behavior.
"""
from __future__ import annotations

import json
import os
import sqlite3

import pytest

from amora.storage import postgres_domain_parity_lab as domain
from amora.storage import postgres_memory_lab as guard
from amora.storage import postgres_v18_lab_schema as pg_schema
from amora.storage import postgres_v18_manifest as v18

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Dedicated disposable local PostgreSQL 18 service only",
)
STAMP = "2026-11-17T00:00:00+00:00"


@pytest.fixture
def pair():
    psycopg = pytest.importorskip("psycopg")
    pg = psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )
    sqlite = v18.synthetic_v18_connection()
    try:
        guard.ensure_local_lab(pg)
        pg.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        pg_schema.install_pg_v18_lab(pg)
        yield sqlite, pg
    finally:
        sqlite.close()
        try:
            pg.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        finally:
            pg.close()


def sqlite_save_entity(sqlite, entity_id, name, kind, aliases=()):
    with sqlite:
        sqlite.execute("""
            INSERT INTO entities(id,canonical_name,entity_type,payload_json)
            VALUES (?,?,?,'{}')
            ON CONFLICT(id) DO UPDATE SET
            canonical_name=excluded.canonical_name,
            entity_type=excluded.entity_type
        """, (entity_id, name, kind))
        sqlite.execute("DELETE FROM aliases WHERE entity_id=?", (entity_id,))
        for alias in sorted(set(aliases) | {name}):
            sqlite.execute(
                "INSERT INTO aliases(normalized_alias,entity_id) VALUES (?,?)",
                (alias, entity_id),
            )


def sqlite_save_relationship(sqlite, source, relation, target, confidence):
    with sqlite:
        sqlite.execute("""
            INSERT INTO relationships(source,relation_type,target,confidence)
            VALUES (?,?,?,?)
            ON CONFLICT(source,relation_type,target)
            DO UPDATE SET confidence=excluded.confidence
        """, (source, relation, target, confidence))


def sqlite_registry_rows(sqlite):
    return (
        sqlite.execute("""
            SELECT id,canonical_name,entity_type FROM entities ORDER BY id
        """).fetchall(),
        sqlite.execute("""
            SELECT normalized_alias,entity_id FROM aliases
            ORDER BY normalized_alias,entity_id
        """).fetchall(),
        sqlite.execute("""
            SELECT source,relation_type,target,confidence FROM relationships
            ORDER BY source,relation_type,target
        """).fetchall(),
    )


def test_identity_names_aliases_and_relationship_upsert_parity(pair):
    sqlite, pg = pair
    for identity, name, kind, aliases in (
        ("bk", "BK", "person", ("bê", "bk")),
        ("amora", "Amora", "person", ("amorinha",)),
        ("friend", "Friend", "person", ()),
    ):
        sqlite_save_entity(sqlite, identity, name, kind, aliases)
        domain.save_entity(pg, entity_id=identity, canonical_name=name,
                           entity_type=kind, aliases=aliases)
    sqlite_save_relationship(sqlite, "bk", "companion", "amora", 0.8)
    domain.save_relationship(pg, source_id="bk", relation="companion",
                              target_id="amora", confidence=0.8)
    sqlite_save_relationship(sqlite, "bk", "companion", "amora", 0.9)
    domain.save_relationship(pg, source_id="bk", relation="companion",
                              target_id="amora", confidence=0.9)
    sqlite_save_entity(sqlite, "bk", "BK", "person", ("bk-new",))
    domain.save_entity(pg, entity_id="bk", canonical_name="BK",
                       entity_type="person", aliases=("bk-new",))
    got = domain.registry_rows(pg)
    expected = sqlite_registry_rows(sqlite)
    assert got == expected
    assert len(got[2]) == 1
    assert got[2][0][-1] == 0.9
    assert ("bê", "bk") not in got[1]


def test_alias_collision_and_broken_relationship_fk_rollback(pair):
    sqlite, pg = pair
    for identity in ("person-a", "person-b"):
        sqlite_save_entity(sqlite, identity, identity, "person")
        domain.save_entity(pg, entity_id=identity, canonical_name=identity,
                           entity_type="person")
    sqlite_save_entity(sqlite, "person-a", "Person A", "person", ("reserved",))
    domain.save_entity(pg, entity_id="person-a", canonical_name="Person A",
                       entity_type="person", aliases=("reserved",))
    psycopg = pytest.importorskip("psycopg")
    with pytest.raises(sqlite3.IntegrityError):
        sqlite_save_entity(sqlite, "person-b", "Changed", "person",
                           ("reserved",))
    with pytest.raises(psycopg.errors.UniqueViolation):
        domain.save_entity(pg, entity_id="person-b", canonical_name="Changed",
                           entity_type="person", aliases=("reserved",))
    assert domain.registry_rows(pg) == sqlite_registry_rows(sqlite)
    with pytest.raises(sqlite3.IntegrityError):
        sqlite_save_relationship(sqlite, "person-a", "knows", "unknown", 0.8)
    with pytest.raises(psycopg.errors.ForeignKeyViolation):
        domain.save_relationship(pg, source_id="person-a", relation="knows",
                                  target_id="unknown", confidence=0.8)
    assert domain.registry_rows(pg) == sqlite_registry_rows(sqlite)


def sqlite_put_decision(sqlite, ident, dedupe, score, payload, created):
    with sqlite:
        try:
            sqlite.execute("""
                INSERT INTO conversation_turn_decisions(
                    id,state,score,result_json,created_at,dedupe_key
                ) VALUES (?,'respond',?,?,?,?)
            """, (ident, score, payload, created, dedupe))
        except sqlite3.IntegrityError:
            return False
    return True


def test_turn_dedupe_and_bounded_listing_parity(pair):
    sqlite, pg = pair
    data = (
        ("turn-1", "de-1", 0.8, json.dumps({"answer": "coelho"}), STAMP),
        ("turn-2", "de-2", 0.9, json.dumps({"answer": "amora"}),
         "2026-11-17T00:01:00+00:00"),
        ("turn-3", "de-3", 0.7, json.dumps({"answer": "floresta"}),
         "2026-11-17T00:02:00+00:00"),
    )
    for ident, dedupe, score, payload, created in data:
        left = sqlite_put_decision(sqlite, ident, dedupe, score, payload, created)
        right = domain.put_turn_decision(
            pg, decision_id=ident, state="respond",
            score=score, payload_json=payload, created_at=created,
            dedupe_key=dedupe,
        )
        assert left is right is True
    for ident, dedupe in (("turn-4", "de-1"), ("turn-1", "de-4")):
        assert sqlite_put_decision(
            sqlite, ident, dedupe, 0.3, "{}", STAMP
        ) is False
        assert domain.put_turn_decision(
            pg, decision_id=ident, state="respond",
            score=0.3, payload_json="{}", created_at=STAMP,
            dedupe_key=dedupe,
        ) is False
    for dedupe in ("de-1", "de-2", "missing"):
        row = sqlite.execute("""
            SELECT result_json FROM conversation_turn_decisions
            WHERE dedupe_key=?
        """, (dedupe,)).fetchone()
        assert domain.get_turn_by_dedupe(pg, dedupe) == (row[0] if row else None)
    for limit in (-10, 1, 2, 2000):
        safe = max(1, min(200, limit))
        source_rows = sqlite.execute("""
            SELECT result_json FROM conversation_turn_decisions
            ORDER BY created_at DESC LIMIT ?
        """, (safe,)).fetchall()
        assert domain.list_turn_results(pg, limit) == [r[0] for r in source_rows]


def sqlite_put_message(sqlite, mid, tid, speaker, session, txt, meta,
                       scope="PERSONAL", remote=False, dedupe=None):
    with sqlite:
        row = sqlite.execute("""
            INSERT INTO conversation_messages(
                id,turn_id,speaker_id,session_id,source,raw_text,
                normalized_text,privacy_scope,remote_processing_allowed,
                status,started_at,metadata_json,dedupe_key
            ) VALUES (?, ?, ?, ?, 'synthetic:domain-lab', ?, ?, ?, ?,
                      'received', ?, ?, ?)
            ON CONFLICT DO NOTHING
        """, (mid, tid, speaker, session, txt, txt, scope,
              int(remote), STAMP, json.dumps(meta), dedupe))
        return row.rowcount > 0


def sqlite_scoped_message(sqlite, mid, speaker, session, remote=False):
    return sqlite.execute("""
        SELECT id,turn_id,raw_text,metadata_json,privacy_scope
        FROM conversation_messages WHERE id=? AND speaker_id=?
          AND session_id=? AND privacy_scope NOT IN ('AUTH','SECRET')
          AND (?=0 OR remote_processing_allowed=1)
    """, (mid, speaker, session, int(remote))).fetchone()


def test_message_deduplication_scope_and_json_serialization(pair):
    sqlite, pg = pair
    cases = (
        ("m1", "turn-a", "bk", "s-1", "oi amora", "PERSONAL", False),
        ("m2", "turn-a", "friend", "s-2", "oi bk", "PUBLIC", True),
        ("m3", "turn-b", "bk", "s-1", "segredo", "SECRET", False),
        ("m4", "turn-b", "bk", "s-1", "credencial", "AUTH", False),
        ("m5", "turn-c", "bk", "s-1", "liberado", "PERSONAL", True),
    )
    for mid, tid, speaker, session, txt, scope, remote in cases:
        metadata = {"turn_id": tid, "synthetic": True}
        dedupe = "synthetic:" + mid
        left = sqlite_put_message(
            sqlite, mid, tid, speaker, session, txt, metadata,
            scope, remote, dedupe,
        )
        right = domain.put_message(
            pg, message_id=mid, turn_id=tid, speaker_id=speaker,
            session_id=session, text=txt,
            metadata_json=json.dumps(metadata), started_at=STAMP,
            dedupe_key=dedupe, privacy_scope=scope,
            remote_processing_allowed=remote,
        )
        assert left is right is True
    # Same logical dedupe key with a different raw message ID cannot overwrite.
    assert sqlite_put_message(
        sqlite, "m6", "turn-d", "bk", "s-1", "injected",
        {}, "PUBLIC", True, "synthetic:m1",
    ) is False
    assert domain.put_message(
        pg, message_id="m6", turn_id="turn-d", speaker_id="bk",
        session_id="s-1", text="injected", metadata_json="{}",
        started_at=STAMP, dedupe_key="synthetic:m1",
        privacy_scope="PUBLIC", remote_processing_allowed=True,
    ) is False

    for mid, tid, speaker, session, txt, scope, remote in cases:
        for as_remote in (False, True):
            expected = sqlite_scoped_message(
                sqlite, mid, speaker, session, as_remote
            )
            actual = domain.scoped_message(
                pg, message_id=mid, speaker_id=speaker, session_id=session,
                remote_only=as_remote,
            )
            assert actual == expected
        assert domain.scoped_message(
            pg, message_id=mid, speaker_id="unknown", session_id=session
        ) is None
        assert domain.scoped_message(
            pg, message_id=mid, speaker_id=speaker, session_id="other"
        ) is None
    assert domain.scoped_message(
        pg, message_id="m3", speaker_id="bk", session_id="s-1"
    ) is None
    assert domain.scoped_message(
        pg, message_id="m4", speaker_id="bk", session_id="s-1"
    ) is None


def test_refuse_remote_database_before_any_sql():
    from types import SimpleNamespace

    class Remote:
        info = SimpleNamespace(
            host="service.aivencloud.com", dbname="amora_pg_synthetic",
            server_version=180000,
        )

        def execute(self, *args, **kwargs):
            raise AssertionError("must not send SQL to Aiven")

        def transaction(self):
            raise AssertionError("must not start transaction")

    with pytest.raises(ValueError, match="POSTGRES_LAB_LOCAL_DB_REQUIRED"):
        domain.registry_rows(Remote())
    with pytest.raises(ValueError, match="POSTGRES_LAB_LOCAL_DB_REQUIRED"):
        domain.put_message(
            Remote(), message_id="n", turn_id="t", speaker_id="s",
            session_id="ss", text="test", metadata_json="{}",
            started_at=STAMP, dedupe_key="dedupe",
        )


def test_reject_invalid_payloads_without_creating_records(pair):
    _, pg = pair
    with pytest.raises(domain.DomainLabError, match="DOMAIN_LAB_INVALID_JSON"):
        domain.put_turn_decision(
            pg, decision_id="t", state="respond", score=0.8,
            payload_json="{broken", created_at=STAMP, dedupe_key="broken",
        )
    with pytest.raises(domain.DomainLabError, match="DOMAIN_LAB_INVALID_PRIVACY_SCOPE"):
        domain.put_message(
            pg, message_id="m", turn_id="t", speaker_id="bk",
            session_id="s", text="fake", metadata_json="{}",
            started_at=STAMP, dedupe_key="d", privacy_scope="NOT_VALID",
        )
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.conversation_turn_decisions
    """).fetchone() == (0,)
    assert pg.execute("""
        SELECT COUNT(*) FROM amora_pg_v18_lab.conversation_messages
    """).fetchone() == (0,)
