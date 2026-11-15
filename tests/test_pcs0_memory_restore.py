"""PCS.0: recover durable memory into an independent Core, never production."""
from datetime import datetime, timezone
import sqlite3

import httpx
import pytest

from amora.app import AmoraCore
from amora.events import PrivacyScope
from amora.memory import MemoryQuery
from amora.storage import LATEST_SCHEMA_VERSION
from amora.storage.remote import LibSQLBackend
from amora.storage.recovery import restore_empty_remote
from amora.time import SimulatedClock
from remote_storage_contracts_v13_source import HranaSQLite, remote
from test_memory_v03 import important_candidate


def test_pcs0_restore_memory_into_fresh_core(remote, tmp_path, monkeypatch):
    backend, server = remote
    clock = SimulatedClock(datetime(2026, 11, 14, 12, tzinfo=timezone.utc))
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "source-runtime"))
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    source = AmoraCore.bootstrap(storage=backend, clock=clock)
    try:
        public = source.memory.remember(important_candidate(
            content="Projeto sintético: desenho aurora de referência.",
            privacy_scope=PrivacyScope.PUBLIC,
            remote_processing_allowed=True,
        )).memory
        private = source.memory.remember(important_candidate(
            content="Projeto sintético: desenho aurora confidencial.",
            privacy_scope=PrivacyScope.PRIVATE,
            owner_entity_id="PERSON_BK",
            remote_processing_allowed=True,
        )).memory
        archived = source.memory.remember(important_candidate(
            content="Projeto sintético: desenho aurora obsoleto.",
            privacy_scope=PrivacyScope.PUBLIC,
        )).memory
        assert public and private and archived
        assert source.memory.archive(archived.id)
        assert not private.remote_processing_allowed
        expected = {
            str(item.id): source.memory.get(item.id).model_dump(mode="json")
            for item in (public, private, archived)
        }
        source.avatar.store.put_asset("pcs-restore-probe", "image/png", b"fixture")
        backup = source.create_backup("pcs0.db")
    finally:
        source.close()

    with sqlite3.connect(backup) as snapshot:
        assert snapshot.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert snapshot.execute("PRAGMA foreign_key_check").fetchone() is None
    target_server = HranaSQLite(tmp_path / "independent-remote.db")
    target_backend = LibSQLBackend(
        "https://restore.example", "test-only",
        transport=httpx.MockTransport(target_server),
    )
    restore_empty_remote(target_backend, backup)
    with pytest.raises(ValueError, match="not empty"):
        restore_empty_remote(target_backend, backup)

    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "restored-runtime"))
    recovered = AmoraCore.bootstrap(storage=target_backend, clock=clock)
    try:
        assert recovered.database.schema_version() == LATEST_SCHEMA_VERSION
        assert recovered.database.integrity_check() == "ok"
        assert recovered.identities.resolve_alias("Nick").id == "PERSON_BK"
        assert recovered.avatar.store.read_asset("pcs-restore-probe")[1] == b"fixture"
        for memory_id, expected_record in expected.items():
            assert recovered.memory.get(memory_id).model_dump(mode="json") == expected_record

        owner_hits = recovered.memory.recall(MemoryQuery(
            text="desenho aurora", entity_ids={"PERSON_BK"},
            projection_recipient_id="PERSON_BK",
        ))
        owner_ids = {hit.memory.id for hit in owner_hits}
        assert {public.id, private.id} <= owner_ids
        assert archived.id not in owner_ids

        remote_hits = recovered.memory.recall(MemoryQuery(
            text="desenho aurora", entity_ids={"PERSON_BK"},
            projection_recipient_id="PERSON_BK", projection_remote_only=True,
        ))
        assert public.id in {hit.memory.id for hit in remote_hits}
        assert private.id not in {hit.memory.id for hit in remote_hits}

        public_hits = recovered.memory.recall(MemoryQuery(
            text="desenho aurora", projection_public_only=True,
            projection_remote_only=True,
        ))
        assert public.id in {hit.memory.id for hit in public_hits}
        assert private.id not in {hit.memory.id for hit in public_hits}
        assert archived.id not in {hit.memory.id for hit in public_hits}
    finally:
        recovered.close()

    # A second boot must read the same independent durable database.
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "second-restored-runtime"))
    reopened = AmoraCore.bootstrap(storage=target_backend, clock=clock)
    try:
        assert reopened.memory.get(public.id).content == public.content
        assert reopened.memory.get(private.id).remote_processing_allowed is False
        assert reopened.memory.get(archived.id).archived_at is not None
    finally:
        reopened.close()
    assert not server.streams
    assert not target_server.streams
