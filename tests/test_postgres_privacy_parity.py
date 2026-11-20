"""Compare PostgreSQL 18 privacy/JSON results to canonical SQLite v18.

All records are invented in memory; no real backup, DSN or personal data.
"""
from contextlib import closing
import json
import os

import pytest

from amora.memory.disclosure import SHAREABLE_MEMORY_SQL
from amora.storage import postgres_memory_lab as guard
from amora.storage import postgres_privacy_lab as dialect
from amora.storage import postgres_synthetic_import as importer
from amora.storage import postgres_v18_lab_schema as pg_schema
from amora.storage import postgres_v18_manifest as source_schema

pytestmark = pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Disposable PostgreSQL 18 in GitHub CI only",
)

STAMP = "2026-11-17T00:00:00+00:00"


@pytest.fixture
def database_pair():
    psycopg = pytest.importorskip("psycopg")
    pg = psycopg.connect(
        host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only",
        connect_timeout=5, autocommit=True,
    )
    source = source_schema.synthetic_v18_connection()
    try:
        guard.ensure_local_lab(pg)
        pg.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        pg_schema.install_pg_v18_lab(pg)
        yield source, pg
    finally:
        source.close()
        try:
            pg.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        finally:
            pg.close()


def add_memory(source, memory_id, *,
               owner="BK", audience=(), actor="BK",
               scope="PERSONAL", remote=1, archived=None,
               valid_until=None, kind="semantic",
               provenance="user_assertion", origin="user:synthetic",
               derived=(), turn_id="turn-1", session="session-1"):
    source.execute(
        """
        INSERT INTO memory_records(
            id,kind,content,provenance_kind,provenance_source,
            provenance_confidence,actor_id,session_id,
            privacy_scope,owner_entity_id,audience_json,
            derived_from_json,remote_processing_allowed,
            valid_until,created_at,updated_at,
            importance,gate_score,archived_at,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            memory_id, kind, "invented test coelho " + memory_id,
            provenance, origin, 1.0, actor, session,
            scope, owner, json.dumps(audience), json.dumps(derived), remote,
            valid_until, STAMP, STAMP, 0.8, 0.8, archived,
            json.dumps({"turn_id": turn_id}),
        ),
    )


def sqlite_projection_ids(source, *, now, recipient=None, public=False,
                          remote=False, archived=False):
    # Exact baseline predicate from canonical MemoryStore._candidate_rows.
    where = ["(valid_until IS NULL OR valid_until >= ?)"]
    params = [now]
    if not archived:
        where.append("archived_at IS NULL")
    if public:
        where.append("privacy_scope = ?")
        params.append("PUBLIC")
    if remote:
        where.append("remote_processing_allowed = 1")
    if recipient and not public:
        where.extend((
            "privacy_scope NOT IN ('AUTH', 'SECRET')",
            "(privacy_scope = 'PUBLIC' OR owner_entity_id = ? OR "
            "EXISTS (SELECT 1 FROM json_each(audience_json) WHERE value = ?) OR "
            "(COALESCE(owner_entity_id, '') = '' AND "
            "json_array_length(audience_json) = 0 AND actor_id = ?))"
        ))
        params.extend([recipient] * 3)
    query = (
        "SELECT id FROM memory_records WHERE " + " AND ".join(where)
        + " ORDER BY (importance * retrievability) DESC,created_at DESC,id LIMIT 300"
    )
    return [row[0] for row in source.execute(query, params)]


def test_recipient_private_public_remote_and_archive_parity(database_pair):
    sqlite, pg = database_pair
    add_memory(sqlite, "own", owner="BK")
    add_memory(sqlite, "audience", owner="OTHER", actor="OTHER", audience=("BK",))
    add_memory(sqlite, "substring", owner="OTHER", actor="OTHER", audience=("BKK",))
    add_memory(sqlite, "ownerless", owner=None, actor="BK")
    add_memory(sqlite, "public", owner="OTHER", actor="OTHER", scope="PUBLIC")
    add_memory(sqlite, "auth", scope="AUTH")
    add_memory(sqlite, "secret", scope="SECRET")
    add_memory(sqlite, "archived", archived=STAMP)
    add_memory(sqlite, "local", remote=0)
    add_memory(sqlite, "expired", valid_until="2025-01-01T00:00:00+00:00")
    sqlite.commit()
    importer.import_synthetic_v18(sqlite, pg)
    conditions = [
        {"recipient": "BK"},
        {"recipient": "BK", "remote": True},
        {"recipient": "BK", "public": True},
        {"recipient": "OTHER"},
        {"recipient": "BK", "archived": True},
    ]
    for opts in conditions:
        expected = sqlite_projection_ids(sqlite, now=STAMP, **opts)
        actual = dialect.projection_candidate_ids(
            pg, now=STAMP, recipient_id=opts.get("recipient"),
            public_only=opts.get("public", False),
            remote_only=opts.get("remote", False),
            include_archived=opts.get("archived", False),
        )
        assert actual == expected, opts
    assert "substring" not in dialect.projection_candidate_ids(
        pg, now=STAMP, recipient_id="BK"
    )
    assert "auth" not in dialect.projection_candidate_ids(
        pg, now=STAMP, recipient_id="BK"
    )
    assert "secret" not in dialect.projection_candidate_ids(
        pg, now=STAMP, recipient_id="BK"
    )


def sqlite_shareable(sqlite, owner, memory_id):
    row = sqlite.execute(
        "SELECT m.id FROM memory_records m WHERE "
        + SHAREABLE_MEMORY_SQL + " AND m.id=?",
        (owner, STAMP, STAMP, memory_id),
    ).fetchone()
    return row[0] if row else None


def test_one_turn_disclosure_json_and_invalidations_parity(database_pair):
    sqlite, pg = database_pair
    add_memory(sqlite, "positive", owner="BK", audience=("BK",))
    add_memory(sqlite, "other-audience", owner="BK", audience=("OTHER",))
    add_memory(sqlite, "derived", owner="BK", derived=("source",))
    add_memory(sqlite, "private", owner="BK", scope="PRIVATE")
    add_memory(sqlite, "other-owner", owner="OTHER", actor="OTHER")
    add_memory(sqlite, "inference", owner="BK", actor="ENTITY_AMORA",
               provenance="inference", origin="cognition:synthetic")
    metadata = {
        "surface": "platform_messaging",
        "channel_type": "dm",
        "test_mode": 0,
        "native_message_id_hash": "fake-hash-01",
        "transport_revision": 1,
    }
    sqlite.execute(
        """
        INSERT INTO conversation_messages(
            id,turn_id,speaker_id,source,privacy_scope,
            session_id,started_at,status,metadata_json,dedupe_key
        ) VALUES (?,?,?,?,?,?,?,?,?,?)
        """,
        (
            "message-1", "turn-1", "BK", "synthetic",
            "PERSONAL", "session-1", STAMP, "received",
            json.dumps(metadata), "cognitive-input:turn-1",
        ),
    )
    sqlite.commit()
    importer.import_synthetic_v18(sqlite, pg)
    for identity in ("positive", "other-audience", "derived",
                     "private", "other-owner", "inference"):
        expected = sqlite_shareable(sqlite, "BK", identity)
        actual = dialect.shareable_owner_record_id(
            pg, owner_id="BK", memory_id=identity, now=STAMP
        )
        assert actual == expected, identity
    assert dialect.shareable_owner_record_id(
        pg, owner_id="BK", memory_id="positive", now=STAMP
    ) == "positive"
    assert dialect.shareable_owner_record_id(
        pg, owner_id="BK", memory_id="inference", now=STAMP
    ) == "inference"
    # A later edit/delete must invalidate the earlier inferred disclosure.
    sqlite.execute(
        """
        INSERT INTO memory_reflection_invalidations(
            session_id,native_message_hash,invalidated_through
        ) VALUES ('session-1','fake-hash-01',1)
        """
    )
    pg.execute(
        """
        INSERT INTO amora_pg_v18_lab.memory_reflection_invalidations(
            session_id,native_message_hash,invalidated_through
        ) VALUES ('session-1','fake-hash-01',1)
        """
    )
    assert sqlite_shareable(sqlite, "BK", "inference") is None
    assert dialect.shareable_owner_record_id(
        pg, owner_id="BK", memory_id="inference", now=STAMP
    ) is None


def test_owner_turn_json_exact_match_and_secret_filter(database_pair):
    sqlite, pg = database_pair
    add_memory(sqlite, "first", turn_id="turn-1")
    add_memory(sqlite, "second", turn_id="turn-11")
    add_memory(sqlite, "other-owner", owner="OTHER", actor="OTHER")
    add_memory(sqlite, "auth", scope="AUTH")
    sqlite.commit()
    importer.import_synthetic_v18(sqlite, pg)
    expected = [
        row[0] for row in sqlite.execute(
            """
            SELECT id FROM memory_records WHERE owner_entity_id=?
            AND privacy_scope NOT IN ('AUTH','SECRET')
            AND json_extract(metadata_json,'$.turn_id')=?
            ORDER BY created_at,id LIMIT 50
            """,
            ("BK", "turn-1"),
        )
    ]
    assert dialect.owner_turn_memory_ids(
        pg, owner_id="BK", turn_id="turn-1"
    ) == expected == ["first"]
