"""Remote storage regression suite aligned with the current schema contract.

The complete historical suite is kept in ``remote_storage_contracts_v13_source``;
this module re-exports it and overrides only the two tests whose schema-version
assertions changed when migration 14 was introduced.
"""

from remote_storage_contracts_v13_source import *  # noqa: F401,F403


def test_remote_migration_constraints_rollback_and_restart(remote, tmp_path):
    backend, server = remote
    db = AmoraDatabase(tmp_path / "unused.db", backend=backend)
    assert db.migrate() == 14
    assert server.closed_transaction_streams > 0
    assert db.migrate() == 0
    event = AmoraEvent(event_type="probe", source="test", dedupe_key="persistent")
    assert db.append_event(event)
    assert not db.append_event(event)
    with pytest.raises(sqlite3.DatabaseError):
        with db._conn:
            db._conn.execute("INSERT INTO avatar_asset_blobs VALUES ('bad', X'')")
    assert db._conn.execute("SELECT COUNT(*) FROM avatar_asset_blobs").fetchone()[0] == 0
    with pytest.raises(sqlite3.DatabaseError):
        with db._conn:
            db._conn.execute("INSERT INTO aliases VALUES ('missing', 'NOT_AN_ENTITY')")
    db.close()
    db = AmoraDatabase(tmp_path / "unused.db", backend=backend)
    assert db.schema_version() == 14
    assert not db.append_event(event)
    db.close()
    assert not server.streams
    assert not (tmp_path / "unused.db").exists()
    assert not any("journal_mode" in q or "synchronous" in q for q in server.calls)


def test_remote_core_memory_web_and_snapshot_with_sql_blobs(remote, tmp_path, monkeypatch):
    backend, server = remote
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "ephemeral"))
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    core = AmoraCore.bootstrap(storage=backend)
    try:
        assert core.health_report()["status"] == "ok"
        assert core.memory.store.fts_enabled
        from test_memory_v03 import important_candidate
        from amora.memory import MemoryQuery

        memory = core.memory.remember(important_candidate()).memory
        assert memory is not None
        hits = core.memory.recall(MemoryQuery(text="desenho", entity_ids={"PERSON_BK"}))
        assert hits and hits[0].memory.id == memory.id
        core.avatar.store.put_asset("sprite", "image/png", b"png-data")
        assert core.avatar.store.read_asset("sprite")[1] == b"png-data"
        assert not list(core.paths.avatar_assets.iterdir())
        headers = {"Authorization": "Bearer " + core.secrets.get("LOCAL_API_TOKEN")}
        with TestClient(create_api(core)) as client:
            response = client.post("/v1/web/utterance", json={"text": "Amora, oi."}, headers=headers)
            assert response.status_code == 200
            assert response.json()["reply_queued"]["device_id"] == "WEB_SAFARI"
            assert client.get("/ready").status_code == 200
        backup = core.create_backup("remote.db")
        with sqlite3.connect(backup) as restored:
            assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert restored.execute("SELECT body FROM avatar_asset_blobs").fetchone()[0] == b"png-data"
        from amora.storage.recovery import restore_empty_remote

        restored_server = HranaSQLite(tmp_path / "restored-server.db")
        restored_backend = LibSQLBackend("https://database.example", "test-only", transport=httpx.MockTransport(restored_server))
        restore_empty_remote(restored_backend, backup)
        with pytest.raises(ValueError, match="not empty"):
            restore_empty_remote(restored_backend, backup)
        check = AmoraDatabase(tmp_path / "unused-restored.db", backend=restored_backend)
        assert check.integrity_check() == "ok"
        assert check.schema_version() == 14
        check.close()
        core.close()
        monkeypatch.setenv("AMORA_HOME", str(tmp_path / "replacement"))
        again = AmoraCore.bootstrap(storage=backend)
        try:
            assert again.avatar.store.read_asset("sprite")[1] == b"png-data"
            assert again.identities.resolve_alias("Nick").id == "PERSON_BK"
            assert again.conversation.store.pending_count() > 0
        finally:
            again.close()
    finally:
        core.close()
    assert not server.streams
