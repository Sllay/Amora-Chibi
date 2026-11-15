"""PCS.2: real sqld schema and native shadow reads, isolated from production."""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import statistics
import time
from pathlib import Path

import httpx
import pytest

from amora.app import AmoraCore
from amora.cognition.store import CognitiveStore
from amora.events import PrivacyScope
from amora.memory import MemoryQuery
from amora.memory.store import MemoryStore
from amora.messaging import ChannelType, RoutingAction
from amora.messaging.models import TransportInvalidation
from amora.storage import LATEST_SCHEMA_VERSION
from amora.storage.backend import SQLiteBackend
from amora.storage.remote import LibSQLBackend
from amora.storage.replica_read import ReplicaReadBackend, ReplicaReadConnection
from amora.storage.replica_coherence import (ReplicaCoherenceError, VersionedReplicaReadGroup,
                                             read_projection_stamp)
from test_memory_v03 import important_candidate
from test_messaging_lifecycle_stability import CountingStructuredProvider, _clock
from test_platform_messaging_step2 import _bind_bk, _message

URL = "http://127.0.0.1:8081"


class ShadowMemoryStore(MemoryStore):
    """Reuse canonical retrieval. No access-counter writes on shadow reads."""
    def _ensure_fts(self):
        return True

    def _mark_accessed(self, memory_ids, now):
        pass


class FakeNative:
    def __init__(self):
        self.connection = sqlite3.connect(":memory:")
        self.connection.execute("CREATE TABLE t (id INTEGER PRIMARY KEY, body TEXT)")
        self.connection.execute("INSERT INTO t VALUES (1, 'fixture')")
        self.fail_sync = False

    def sync(self):
        if self.fail_sync:
            raise RuntimeError("synthetic refresh failure")

    def execute(self, *args):
        return self.connection.execute(*args)

    def close(self):
        self.connection.close()


def test_pcs2_read_view_requires_sync_and_denies_mutations():
    native = FakeNative()
    view = ReplicaReadConnection(native)
    try:
        with pytest.raises(sqlite3.OperationalError, match="NOT_SYNCHRONIZED"):
            view.execute("SELECT * FROM t")
        view.sync()
        row = view.execute("SELECT * FROM t WHERE id=?", (1,)).fetchone()
        assert row[0] == row["id"] == 1
        assert dict(row) == {"id": 1, "body": "fixture"}
        for sql in ("UPDATE t SET body='bad'", "DELETE FROM t", "PRAGMA query_only=OFF",
                    "SELECT * FROM t; DELETE FROM t", "BEGIN IMMEDIATE"):
            with pytest.raises(sqlite3.OperationalError, match="READ_ONLY"):
                view.execute(sql)
        with pytest.raises(sqlite3.OperationalError, match="READ_ONLY"):
            with view:
                pass
        view.invalidate()
        with pytest.raises(sqlite3.OperationalError, match="NOT_SYNCHRONIZED"):
            view.execute("SELECT * FROM t")
        view.sync()
        assert view.execute("SELECT count(*) FROM t").fetchone()[0] == 1
    finally:
        view.close()


def test_pcs2_partial_refresh_never_leaves_some_views_ready():
    native = [FakeNative(), FakeNative()]
    backend = ReplicaReadBackend(lambda _: native.pop(0))
    first, second = backend.connect(Path("one")), backend.connect(Path("two"))
    try:
        backend.sync_all()
        second._connection.fail_sync = True
        with pytest.raises(RuntimeError, match="synthetic refresh failure"):
            backend.sync_all()
        for view in (first, second):
            with pytest.raises(sqlite3.OperationalError, match="NOT_SYNCHRONIZED"):
                view.execute("SELECT * FROM t")
        second._connection.fail_sync = False
        backend.sync_all()
        assert first.execute("SELECT count(*) FROM t").fetchone()[0] == 1
    finally:
        first.close()
        second.close()


def rows(connection, sql, args=()):
    return [tuple(row.values) for row in connection.execute(sql, args).fetchall()]


def assert_full_schema(primary, replica):
    sql = ("SELECT type,name,tbl_name,sql FROM sqlite_schema "
           "WHERE sql IS NOT NULL ORDER BY type,name")
    assert rows(primary, sql) == rows(replica, sql), "schema mismatch"
    table_names = [row[0] for row in primary.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall() if not row[0].startswith("memory_fts_")]
    for name in table_names:
        quoted = '"' + name.replace('"', '""') + '"'
        expected = rows(primary, "SELECT * FROM " + quoted)
        actual = rows(replica, "SELECT * FROM " + quoted)
        assert sorted(expected, key=repr) == sorted(actual, key=repr), f"table mismatch: {name}"
    assert replica.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert replica.execute("PRAGMA foreign_key_check").fetchone() is None
    return len(table_names)


def install_lab_epoch(primary):
    """Synthetic additive revision mechanism; deliberately not a runtime migration.

    All mutations in the selected memory/history projection must update this
    row in the same primary transaction. FTS is maintained by the canonical
    memory write transaction, not independently in this fixture.
    """
    tables = ("memory_records", "memory_entities", "memory_tags",
              "conversation_messages", "memory_reflection_invalidations",
              "entities", "relationships")
    with primary:
        primary.execute("CREATE TABLE amora_projection_epoch (singleton INTEGER PRIMARY KEY "
                        "CHECK(singleton=1), epoch_id TEXT NOT NULL, revision INTEGER NOT NULL "
                        "CHECK(revision>=0))")
        primary.execute("INSERT INTO amora_projection_epoch VALUES (1,'pcs2-fixture',0)")
        for table in tables:
            for verb in ("INSERT", "UPDATE", "DELETE"):
                primary.execute(f"CREATE TRIGGER pcs2_epoch_{table}_{verb.lower()} "
                                f"AFTER {verb} ON {table} BEGIN "
                                "UPDATE amora_projection_epoch SET revision=revision+1 "
                                "WHERE singleton=1; END")


@pytest.mark.skipif(os.getenv("AMORA_PCS2_NATIVE_LAB") != "1",
                    reason="requires isolated loopback sqld fixture on port 8081")
@pytest.mark.asyncio
async def test_pcs2_native_full_schema_and_shadow_reads(tmp_path, monkeypatch, capsys):
    import libsql

    for key in tuple(os.environ):
        if key.startswith("AMORA_SECRET_") or key in {"AMORA_MODE", "AMORA_SOCIAL_PRIMARY",
                                                     "AMORA_STORAGE_BACKEND"}:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "primary-runtime"))
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    monkeypatch.setenv("AMORA_SOCIAL_EXPRESSION_PROFILE", "amora-v3")
    # Keep the actual Core Hrana adapter, but map its test-only HTTPS origin
    # to a disposable local server. No production credentials are accepted.
    transport_calls = 0
    transport_delay = 0.0
    with httpx.Client(trust_env=False, timeout=20) as client:
        def transport(request):
            nonlocal transport_calls
            transport_calls += 1
            if transport_delay:
                time.sleep(transport_delay)
            assert request.url.host == "database.example"
            assert request.url.path == "/v2/pipeline"
            response = client.post(URL + "/v2/pipeline", content=request.content,
                                   headers={"content-type": "application/json"})
            return httpx.Response(response.status_code, content=response.content,
                                  headers={"content-type": "application/json"})
        primary_backend = LibSQLBackend("https://database.example", "fixture-only",
                                        transport=httpx.MockTransport(transport))
        probe = primary_backend.connect(tmp_path / "unused")
        try:
            assert probe.execute("SELECT COUNT(*) FROM sqlite_schema WHERE type='table' "
                                 "AND name NOT LIKE 'sqlite_%'").fetchone()[0] == 0
        finally:
            probe.close()
        core = AmoraCore.bootstrap(clock=_clock(), storage=primary_backend)
        replica_root = tmp_path / "replicas"
        replica_root.mkdir()
        counter = 0
        def factory(_):
            nonlocal counter
            counter += 1
            return libsql.connect(str(replica_root / f"view-{counter}.db"),
                                  sync_url=URL, auth_token="", isolation_level=None)
        replica = ReplicaReadBackend(factory)
        shadow_memory = shadow_history = raw = None
        try:
            assert core.database.schema_version() == LATEST_SCHEMA_VERSION
            _bind_bk(core)
            pub = core.memory.remember(important_candidate(
                content="Desenho aurora público: projeto sintético.",
                privacy_scope=PrivacyScope.PUBLIC, remote_processing_allowed=True)).memory
            private = core.memory.remember(important_candidate(
                content="Desenho aurora privado: projeto sintético.",
                privacy_scope=PrivacyScope.PRIVATE, owner_entity_id="PERSON_BK",
                remote_processing_allowed=True)).memory
            assert pub and private
            asset = core.avatar.store.put_asset("pcs2-synthetic", "image/png", b"fixture-pcs2")
            provider = CountingStructuredProvider(name="pcs2-offline")
            core.cognition.register_provider(provider)
            message = _message(core, message_id="pcs2-message", actor_id="111111111111111111",
                               channel_type=ChannelType.DM, conversation_id="pcs2-dm",
                               text="Amora, o que acha do desenho aurora?").model_copy(
                                   update={"remote_processing_allowed": True})
            result = await core.messaging.receive(message)
            assert result.decision.action == RoutingAction.RESPOND
            assert result.outbound[0].text == "Resposta remota estável."
            assert provider.calls == 1

            raw = replica.connect(tmp_path / "raw")
            shadow_memory = ShadowMemoryStore(tmp_path / "memory", core.clock, core.identities,
                                              backend=replica)
            shadow_history = CognitiveStore(tmp_path / "history", core.clock, backend=replica)
            started = time.perf_counter()
            replica.sync_all()
            sync_ms = (time.perf_counter() - started) * 1000
            primary = core.database._conn
            tables = assert_full_schema(primary, raw)
            assert raw.execute("SELECT body FROM avatar_asset_blobs WHERE file_name=?",
                               (asset.file_name,)).fetchone()[0] == b"fixture-pcs2"
            query = MemoryQuery(text="desenho aurora", entity_ids={"PERSON_BK"},
                                projection_recipient_id="PERSON_BK", projection_remote_only=True)
            started = time.perf_counter()
            shadow_hits = shadow_memory.search(query)
            local_recall_ms = (time.perf_counter() - started) * 1000
            primary_hits = core.memory.recall(query)
            assert [h.model_dump(mode="json") for h in shadow_hits] == [
                h.model_dump(mode="json") for h in primary_hits]
            assert pub.id in {hit.memory.id for hit in shadow_hits}
            assert private.id not in {hit.memory.id for hit in shadow_hits}

            session = core.messaging._session_ref(message)
            assert shadow_history.list_messages(session_id=session, exclude_invalidated=True) == (
                core.cognition.store.list_messages(session_id=session, exclude_invalidated=True))
            assert shadow_history.list_messages(session_id=session)

            # PCS.2D: all shadow results pass a primary revision fence before
            # leaving the group. The laboratory stamp is maintained by triggers,
            # not a wall-clock TTL or a process-local invalidation flag.
            install_lab_epoch(primary)
            group = VersionedReplicaReadGroup(replica, lambda: read_projection_stamp(primary))
            primary_shadow = ShadowMemoryStore(tmp_path / "primary-shadow", core.clock,
                                               core.identities, backend=primary_backend)
            writer = primary_backend.connect(tmp_path / "independent-writer")
            def local_pair():
                return (shadow_memory.search(query),
                        shadow_history.list_messages(session_id=session, exclude_invalidated=True))
            def remote_pair():
                return (primary_shadow.search(query),
                        core.cognition.store.list_messages(session_id=session, exclude_invalidated=True))
            try:
                started = time.perf_counter()
                cold = group.read(local_pair)
                cold_ms = (time.perf_counter() - started) * 1000
                assert cold == remote_pair()
                assert pub.id in {hit.memory.id for hit in cold[0]}
                with pytest.raises(sqlite3.OperationalError, match="NOT_SYNCHRONIZED"):
                    local_pair()
                # Paired hot read group with identical synthetic primary RTT;
                # native refresh is not part of the warm samples.
                paired = {"remote": [], "local": []}
                transport_delay = .020
                for _ in range(3):
                    for name, operation in (("remote", remote_pair),
                                            ("local", lambda: group.read(local_pair))):
                        before = transport_calls
                        tick = time.perf_counter()
                        assert operation() == cold
                        paired[name].append(((time.perf_counter() - tick) * 1000,
                                             transport_calls - before))
                transport_delay = 0
                assert all(calls == 2 for _, calls in paired["local"])
                assert min(calls for _, calls in paired["remote"]) > 2
                fence_metrics = {
                    "cold_sync_and_read_ms": round(cold_ms, 3),
                    "hot_local_median_ms": round(statistics.median(
                        ms for ms, _ in paired["local"]), 3),
                    "hot_remote_median_ms": round(statistics.median(
                        ms for ms, _ in paired["remote"]), 3),
                    "hot_local_primary_calls": 2,
                    "hot_remote_primary_calls": paired["remote"][0][1],
                    "synthetic_primary_rtt_ms": 20, "paired_samples": 3,
                }

                before = read_projection_stamp(primary)
                with pytest.raises(RuntimeError, match="fixture rollback"):
                    with writer:
                        writer.execute("UPDATE memory_records SET remote_processing_allowed=0 WHERE id=?",
                                       (str(pub.id),))
                        raise RuntimeError("fixture rollback")
                assert read_projection_stamp(primary) == before
                assert pub.id in {hit.memory.id for hit in group.read(local_pair)[0]}

                # An independent authoritative writer revokes disclosure after
                # local retrieval but before the final primary stamp check.
                def revoke_during_read():
                    old = local_pair()
                    with writer:
                        writer.execute("UPDATE memory_records SET remote_processing_allowed=0 WHERE id=?",
                                       (str(pub.id),))
                    return old
                with pytest.raises(ReplicaCoherenceError, match="CHANGED_DURING_READ"):
                    group.read(revoke_during_read)
                with pytest.raises(sqlite3.OperationalError, match="NOT_SYNCHRONIZED"):
                    local_pair()
                assert pub.id not in {hit.memory.id for hit in group.read(local_pair)[0]}
                with writer:
                    writer.execute("UPDATE memory_records SET remote_processing_allowed=1 WHERE id=?",
                                   (str(pub.id),))
                assert pub.id in {hit.memory.id for hit in group.read(local_pair)[0]}
                fence_metrics["revocation_race_rejected"] = True
                fence_metrics["rollback_stamp_unchanged"] = True
            finally:
                transport_delay = 0
                writer.close()
                primary_shadow.close()

            # Canonical mutations are visible on the next verified read without
            # an explicit caller invalidation or a fresh sync on every hot read.
            assert core.memory.archive(pub.id)
            core.messaging.invalidate_transport(TransportInvalidation(
                platform=message.platform, account_scope=message.account_scope,
                conversation_id=message.conversation_id, native_message_id=message.native_source_message_id,
                reason="DISCORD_MESSAGE_UPDATED", transport_revision=2))
            updated_hits, updated_history = group.read(local_pair)
            assert pub.id not in {hit.memory.id for hit in updated_hits}
            assert updated_history == []
            group.read(lambda: assert_full_schema(primary, raw))

            # Rebuild all views after local files are physically removed.
            shadow_memory.close()
            shadow_history.close()
            raw.close()
            shadow_memory = shadow_history = raw = None
            shutil.rmtree(replica_root)
            replica_root.mkdir()
            rebuilt = ReplicaReadBackend(factory)
            raw = rebuilt.connect(tmp_path / "rebuilt")
            rebuilt.sync_all()
            assert_full_schema(primary, raw)
            assert raw.execute("SELECT archived_at FROM memory_records WHERE id=?",
                               (str(pub.id),)).fetchone()[0] is not None
            report = {"schema_version": LATEST_SCHEMA_VERSION, "tables_verified": tables,
                      "sync_three_views_ms": round(sync_ms, 3),
                      "shadow_recall_ms": round(local_recall_ms, 3),
                      "full_schema_equal": True, "memory_ranking_equal": True,
                      "history_equal": True, "archive_and_edit_after_sync": True,
                      "rebuild_after_disk_loss": True, "version_fence": fence_metrics}
            with capsys.disabled():
                print("PCS2_NATIVE_JSON=" + json.dumps(report, sort_keys=True), flush=True)
        finally:
            for view in (shadow_memory, shadow_history, raw):
                if view is not None:
                    view.close()
            await core.messaging.stop_inflight()
            core.close()
