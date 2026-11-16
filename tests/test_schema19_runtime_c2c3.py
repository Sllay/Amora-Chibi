"""C.2C.3: explicit runtime v19 opt-in; no implicit upgrade on deployment."""
from __future__ import annotations

import sqlite3

import httpx
import pytest

from amora.app import AmoraCore
from amora.events import AmoraEvent
from amora.storage import AmoraDatabase, LATEST_SCHEMA_VERSION
from amora.storage.backend import SQLiteBackend
from amora.storage.remote import LibSQLBackend
from amora.storage.schema import runtime_schema_target
from amora.storage.sensory_episode_schema import (
    CANDIDATE_TABLES, verify_candidate_ddl,
)
from remote_storage_contracts_v13_source import HranaSQLite


CONFIRM = "CERTIFIED_V19_BACKUP_AND_ROLLBACK"


def enable_v19(monkeypatch):
    monkeypatch.setenv("AMORA_RUNTIME_SCHEMA_TARGET", "19")
    monkeypatch.setenv("AMORA_RUNTIME_SCHEMA_CONFIRMATION", CONFIRM)


@pytest.mark.parametrize("target,confirmation", [
    ("19", ""), ("19", "unverified"), ("20", CONFIRM),
    ("garbage", ""), ("18", CONFIRM), ("", CONFIRM),
])
def test_runtime_target_fails_closed(target, confirmation, monkeypatch, tmp_path):
    monkeypatch.setenv("AMORA_RUNTIME_SCHEMA_TARGET", target)
    monkeypatch.setenv("AMORA_RUNTIME_SCHEMA_CONFIRMATION", confirmation)
    with pytest.raises(ValueError, match="SCHEMA19_RUNTIME"):
        runtime_schema_target()
    with pytest.raises(ValueError, match="SCHEMA19_RUNTIME"):
        AmoraDatabase(tmp_path / "should-not-exist.db")
    assert not (tmp_path / "should-not-exist.db").exists()


def test_default_remains_v18_without_implicit_upgrade(tmp_path, monkeypatch):
    monkeypatch.delenv("AMORA_RUNTIME_SCHEMA_TARGET", raising=False)
    monkeypatch.delenv("AMORA_RUNTIME_SCHEMA_CONFIRMATION", raising=False)
    assert LATEST_SCHEMA_VERSION == 18
    db = AmoraDatabase(tmp_path / "default.db")
    try:
        assert db.expected_schema_version == 18
        assert db.migrate() == 18
        assert db.migrate() == 0
        assert db.schema_version() == 18
        assert not {x[0] for x in db._conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='table'"
        )} & CANDIDATE_TABLES
    finally:
        db.close()


def test_v18_to_v19_preserves_events_and_restarts(tmp_path, monkeypatch):
    path = tmp_path / "preserved.db"
    db = AmoraDatabase(path)
    try:
        db.migrate()
        assert db.append_event(AmoraEvent(event_type="schema19-probe", source="test",
                                          dedupe_key="schema19-history"))
        historical = db._conn.execute(
            "SELECT id, event_type, payload_json FROM events ORDER BY id"
        ).fetchall()
        history = db._conn.execute(
            "SELECT version, applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
    finally:
        db.close()

    enable_v19(monkeypatch)
    upgraded = AmoraDatabase(path)
    try:
        assert upgraded.migrate() == 1
        assert upgraded.schema_version() == 19
        verify_candidate_ddl(upgraded._conn)
        assert {row[0] for row in upgraded._conn.execute(
            "SELECT name FROM sqlite_schema WHERE type='table'"
        )} >= CANDIDATE_TABLES
        assert upgraded._conn.execute(
            "SELECT id,event_type,payload_json FROM events ORDER BY id"
        ).fetchall() == historical
        new_history = upgraded._conn.execute(
            "SELECT version,applied_at FROM schema_migrations ORDER BY version"
        ).fetchall()
        assert new_history[:-1] == history
        assert new_history[-1][0] == 19
        assert upgraded.migrate() == 0
    finally:
        upgraded.close()

    again = AmoraDatabase(path)
    try:
        assert again.migrate() == 0
        assert again.schema_version() == 19
        verify_candidate_ddl(again._conn)
    finally:
        again.close()

    monkeypatch.delenv("AMORA_RUNTIME_SCHEMA_TARGET", raising=False)
    monkeypatch.delenv("AMORA_RUNTIME_SCHEMA_CONFIRMATION", raising=False)
    older = AmoraDatabase(path)
    try:
        with pytest.raises(ValueError, match="RUNTIME_SCHEMA_AHEAD_OF_ALLOWED_TARGET"):
            older.migrate()
        assert older.schema_version() == 19
    finally:
        older.close()


def test_partial_v19_objects_are_not_overwritten(tmp_path, monkeypatch):
    path = tmp_path / "partial.db"
    db = AmoraDatabase(path)
    try:
        db.migrate()
        with db._conn:
            db._conn.execute(
                "CREATE TABLE sensory_episode_invalidations "
                "(event_id TEXT PRIMARY KEY, invalidated_at TEXT NOT NULL)"
            )
    finally:
        db.close()
    enable_v19(monkeypatch)
    after = AmoraDatabase(path)
    try:
        with pytest.raises(ValueError, match="SCHEMA19_PREEXISTING_CANDIDATE_OBJECTS"):
            after.migrate()
        assert after.schema_version() == 18
        assert after._conn.execute(
            "SELECT COUNT(*) FROM sqlite_schema WHERE name='sensory_episode_invalidations'"
        ).fetchone()[0] == 1
    finally:
        after.close()


def test_tampered_v19_schema_does_not_boot(tmp_path, monkeypatch):
    enable_v19(monkeypatch)
    db = AmoraDatabase(tmp_path / "bad.db")
    try:
        db.migrate()
        with db._conn:
            db._conn.execute("DROP INDEX idx_sensory_episode_due")
        with pytest.raises(ValueError, match="SCHEMA19_DDL_MISMATCH"):
            db.migrate()
    finally:
        db.close()


def test_ready_core_on_opted_in_v19_and_no_auto_downgrade(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "core"))
    monkeypatch.setenv("AMORA_MODE", "local")
    enable_v19(monkeypatch)
    core = AmoraCore.bootstrap(storage=SQLiteBackend())
    try:
        assert core.database.schema_version() == 19
        assert core.readiness()["status"] == "ready"
        assert core.readiness()["storage"] == "sqlite"
    finally:
        core.close()
    # An older deployment must refuse to bootstrap a newer durable DB.
    monkeypatch.delenv("AMORA_RUNTIME_SCHEMA_TARGET", raising=False)
    monkeypatch.delenv("AMORA_RUNTIME_SCHEMA_CONFIRMATION", raising=False)
    with pytest.raises(ValueError, match="RUNTIME_SCHEMA_AHEAD_OF_ALLOWED_TARGET"):
        AmoraCore.bootstrap(storage=SQLiteBackend())


def test_isolated_hrana_runtime_upgrade_additive(tmp_path, monkeypatch):
    server = HranaSQLite(tmp_path / "hrana.db")
    backend = LibSQLBackend("https://c2c3-isolated.example", "c2c3-test-token",
                            transport=httpx.MockTransport(server))
    database = AmoraDatabase(tmp_path / "unused.db", backend=backend)
    try:
        assert database.migrate() == 18
        assert database.schema_version() == 18
    finally:
        database.close()
    with sqlite3.connect(server.path) as db:
        previous = db.execute("SELECT version, applied_at FROM schema_migrations ORDER BY version").fetchall()
    enable_v19(monkeypatch)
    migrated = AmoraDatabase(tmp_path / "unused.db", backend=backend)
    try:
        assert migrated.migrate() == 1
        assert migrated.schema_version() == 19
        verify_candidate_ddl(migrated._conn)
        assert migrated.migrate() == 0
    finally:
        migrated.close()
    with sqlite3.connect(server.path) as db:
        current = db.execute("SELECT version, applied_at FROM schema_migrations ORDER BY version").fetchall()
        assert current[:-1] == previous
        assert current[-1][0] == 19
    assert not server.streams
