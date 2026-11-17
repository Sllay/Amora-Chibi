"""Turso row-read regressions: indexable polling with unchanged delivery semantics.

Never connects to the user's database. SQLite's EXPLAIN QUERY PLAN is exercised
against the original schema v10/v13, which is also used by schema v18.
"""
from __future__ import annotations

import sqlite3

from amora.storage.migrations_v10 import MIGRATION_V10
from amora.storage.migrations_v103 import MIGRATION_V103
from amora.messaging import reflection_queue as reflection_module


EMPTY_OUTBOUND_PROBE = """
    SELECT 1 FROM messaging_outbound_queue
    WHERE platform=? AND account_scope=? AND status='queued'
    UNION ALL
    SELECT 1 FROM messaging_outbound_queue
    WHERE platform=? AND account_scope=? AND status='claimed'
      AND claim_expires_at < ?
    LIMIT 1
"""

INDEXED_REFLECTION_CANDIDATES = """
    SELECT m.turn_id FROM conversation_messages m
    WHERE m.dedupe_key >= 'cognitive-input:'
      AND m.dedupe_key < 'cognitive-input;'
      AND m.dedupe_key = 'cognitive-input:' || m.turn_id
      AND m.remote_processing_allowed = 1
    ORDER BY m.started_at,m.turn_id LIMIT 32
"""


def test_outbound_poll_uses_existing_status_index_for_both_states():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(MIGRATION_V103[1])
        plan = conn.execute(
            "EXPLAIN QUERY PLAN " + EMPTY_OUTBOUND_PROBE,
            ("discord", "primary", "discord", "primary", "2026-11-17T00:00:00Z"),
        ).fetchall()
        seeks = [
            row[3] for row in plan
            if "SEARCH messaging_outbound_queue" in row[3]
        ]
        assert len(seeks) == 2, plan
        assert all("idx_messaging_outbound_claim" in step for step in seeks), plan
        assert not any("SCAN messaging_outbound_queue" in row[3] for row in plan)

        rows = [
            ("sent", "2026-11-14T00:00:00Z"),
            ("claimed", "2026-11-18T00:00:00Z"),
        ]
        for i, (status, expiration) in enumerate(rows):
            conn.execute(
                """INSERT INTO messaging_outbound_queue
                   (id,platform,account_scope,conversation_id,text,created_at,status,claim_expires_at)
                   VALUES (?, 'discord','primary','c','hi',?, ?, ?)""",
                (str(i), "2026-11-16T00:00:00Z", status, expiration),
            )
        args = ("discord", "primary", "discord", "primary", "2026-11-17T00:00:00Z")
        assert conn.execute(EMPTY_OUTBOUND_PROBE, args).fetchone() is None
        conn.execute("UPDATE messaging_outbound_queue SET status='queued' WHERE id='0'")
        assert conn.execute(EMPTY_OUTBOUND_PROBE, args).fetchone()[0] == 1
        conn.execute("UPDATE messaging_outbound_queue SET status='sent' WHERE id='0'")
        conn.execute(
            "UPDATE messaging_outbound_queue SET claim_expires_at=? WHERE id='1'",
            ("2026-11-16T00:00:00Z",),
        )
        assert conn.execute(EMPTY_OUTBOUND_PROBE, args).fetchone()[0] == 1
    finally:
        conn.close()


def test_reflection_candidates_use_existing_unique_dedupe_index():
    conn = sqlite3.connect(":memory:")
    try:
        conn.executescript(MIGRATION_V10[1])
        # Mixed ordinary messages vs explicitly requested cognitive inputs.
        rows = [
            (
                str(i), "turn-" + str(i), "2026-11-17T00:00:00Z",
                ("cognitive-input:turn-" if i % 100 == 0 else "other:") + str(i),
                int(i % 100 == 0),
            )
            for i in range(500)
        ]
        conn.executemany(
            """INSERT INTO conversation_messages
               (id,turn_id,source,privacy_scope,status,started_at,dedupe_key,remote_processing_allowed)
               VALUES (?,?,'test','PERSONAL','received',?,?,?)""",
            rows,
        )
        conn.execute("ANALYZE")
        plan = conn.execute(
            "EXPLAIN QUERY PLAN " + INDEXED_REFLECTION_CANDIDATES,
        ).fetchall()
        assert any(
            "SEARCH m USING INDEX sqlite_autoindex_conversation_messages" in row[3]
            and "dedupe_key>?" in row[3]
            for row in plan
        ), plan
        assert not any("SCAN m" in row[3] for row in plan)
        assert [row[0] for row in conn.execute(INDEXED_REFLECTION_CANDIDATES)] == [
            "turn-0", "turn-100", "turn-200", "turn-300", "turn-400"
        ]
        # The canonical comparison of key and turn_id remains authoritative:
        # no other prefix or malformed cognitive-input row is eligible.
    finally:
        conn.close()


def test_reflection_background_sweep_is_bounded_but_not_disabled():
    assert 60 <= reflection_module._RECONCILE_SECONDS <= 300
    assert reflection_module._IDLE_POLL_SECONDS == 10
