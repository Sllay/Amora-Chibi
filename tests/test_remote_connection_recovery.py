import logging
import sqlite3
from uuid import uuid4

import httpx
import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveJournalEntry, JournalStage
from amora.storage.remote import LibSQLBackend, StorageUnavailable
from hrana_faults import FaultInjectingHrana


@pytest.fixture
def fault_remote(tmp_path):
    server = FaultInjectingHrana(tmp_path / "server.db")
    backend = LibSQLBackend(
        "https://database.example",
        "recovery-db-token",
        transport=httpx.MockTransport(server),
    )
    yield backend, server
    for connection in server.streams.values():
        connection.close()
    server.streams.clear()


@pytest.mark.parametrize(
    "sql",
    ["INSERT INTO probe VALUES (1)", "UPDATE probe SET value=2", "DELETE FROM probe"],
)
@pytest.mark.parametrize("before", [False, True])
def test_ambiguous_write_is_not_retried_and_next_operation_uses_fresh_client(
    fault_remote, tmp_path, caplog, sql, before
):
    backend, server = fault_remote
    connection = backend.connect(tmp_path / "unused")
    connection.execute("CREATE TABLE probe(value INTEGER)")
    connection.execute("INSERT INTO probe VALUES (0)")
    old_client = connection.client
    server.arm(sql, before=before)
    caplog.set_level(logging.INFO, logger="amora.storage.remote")
    try:
        request_count_before = len(server.requests)
        with pytest.raises(
            StorageUnavailable, match="^SQL_TRANSPORT_FAILED_OUTCOME_UNKNOWN$"
        ):
            connection.execute(sql)
        assert len(server.requests) == request_count_before + 1
        assert server.count_sql(sql) == 1
        assert connection._broken and not connection._transaction
        expected = (
            1
            if before
            else (
                2 if sql.startswith("INSERT") else 1 if sql.startswith("UPDATE") else 0
            )
        )
        assert (
            connection.execute("SELECT COUNT(*) FROM probe").fetchone()[0] == expected
        )
        assert server.count_sql(sql) == 1
        assert old_client.is_closed and connection.client is not old_client
        assert server.requests[-1]["baton"] is None
        assert (
            server.requests[-1]["requests"][0]["stmt"]["sql"]
            == "PRAGMA foreign_keys=ON"
        )
        events = [r for r in caplog.records if r.name == "amora.storage.remote"]
        assert [r.message for r in events] == [
            "storage_connection_poisoned",
            "storage_connection_recovered",
        ]
        assert events[-1].previous_error == "SQL_TRANSPORT_FAILED_OUTCOME_UNKNOWN"
        assert events[0].exception_type == "ReadTimeout"
        assert events[0].connection_id == events[-1].connection_id
        assert not any(
            secret in caplog.text
            for secret in ["recovery-db-token", "private-body", "do-not-log", sql]
        )
    finally:
        connection.close()


def test_broken_transaction_never_recovers_until_context_is_abandoned(
    fault_remote, tmp_path
):
    backend, server = fault_remote
    connection = backend.connect(tmp_path / "unused")
    connection.execute("CREATE TABLE probe(value INTEGER)")
    old_client = connection.client
    server.arm("INSERT INTO probe")
    with pytest.raises(StorageUnavailable, match="SQL_CONNECTION_CLOSED"):
        with connection:
            with pytest.raises(StorageUnavailable, match="OUTCOME_UNKNOWN"):
                connection.execute("INSERT INTO probe VALUES (1)")
            before = len(server.requests)
            with pytest.raises(StorageUnavailable, match="SQL_CONNECTION_CLOSED"):
                connection.execute("SELECT 1")
            assert len(server.requests) == before
            assert connection.client is old_client
    assert not connection._transaction
    assert connection.execute("SELECT COUNT(*) FROM probe").fetchone()[0] == 0
    assert server.count_sql("INSERT INTO probe") == 1
    assert server.count_sql("COMMIT") == 0 and server.count_sql("ROLLBACK") == 0
    connection.close()


@pytest.mark.parametrize("terminator", ["COMMIT", "ROLLBACK"])
def test_ambiguous_terminator_does_not_retry_and_next_transaction_is_fresh(
    fault_remote, tmp_path, terminator
):
    backend, server = fault_remote
    connection = backend.connect(tmp_path / "unused")
    connection.execute("CREATE TABLE probe(value INTEGER)")
    server.arm(terminator)
    with pytest.raises(StorageUnavailable, match="OUTCOME_UNKNOWN"):
        with connection:
            connection.execute("INSERT INTO probe VALUES (1)")
            if terminator == "ROLLBACK":
                raise ValueError("abandon")
    assert server.count_sql(terminator) == 1 and not connection._transaction
    old_client = connection.client
    boundary = len(server.requests)
    with connection:
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM probe").fetchone()[0] == (
            terminator == "COMMIT"
        )
    assert old_client.is_closed and connection.client is not old_client
    assert server.requests[boundary]["baton"] is None
    assert server.count_sql("INSERT INTO probe") == 1
    assert not connection._broken
    connection.close()


def test_failed_begin_does_not_explicitly_close_the_store_connection(
    fault_remote, tmp_path
):
    backend, server = fault_remote
    connection = backend.connect(tmp_path / "unused")
    server.arm("BEGIN IMMEDIATE")
    with pytest.raises(StorageUnavailable, match="OUTCOME_UNKNOWN"):
        with connection:
            pytest.fail("BEGIN never completed")
    assert not connection._closed and not connection._transaction
    assert server.count_sql("BEGIN IMMEDIATE") == 1
    with connection:
        assert connection.execute("SELECT 1").fetchone()[0] == 1
    assert (
        server.count_sql("BEGIN IMMEDIATE") == 2
    )  # Independent context, no retry of the failed context.
    connection.close()


@pytest.mark.parametrize("poisoned", [False, True])
def test_explicit_close_is_terminal_even_when_poisoned(
    fault_remote, tmp_path, poisoned
):
    backend, server = fault_remote
    connection = backend.connect(tmp_path / "unused")
    if poisoned:
        server.arm("SELECT 1")
        with pytest.raises(StorageUnavailable):
            connection.execute("SELECT 1")
    connection.close()
    before = len(server.requests)
    for operation in [
        lambda: connection.execute("SELECT 1"),
        lambda: connection.__enter__(),
    ]:
        with pytest.raises(StorageUnavailable, match="^SQL_CONNECTION_CLOSED$"):
            operation()
    assert len(server.requests) == before


def test_owned_script_transaction_is_abandoned_after_transport_loss(
    fault_remote, tmp_path
):
    backend, server = fault_remote
    connection = backend.connect(tmp_path / "unused")
    server.arm("INSERT INTO probe")
    with pytest.raises(StorageUnavailable, match="OUTCOME_UNKNOWN"):
        connection.executescript(
            "BEGIN; CREATE TABLE probe(value); INSERT INTO probe VALUES (1); COMMIT;"
        )
    assert not connection._transaction
    assert connection.execute("SELECT 1").fetchone()[0] == 1
    assert (
        server.count_sql("INSERT INTO probe") == 1 and server.count_sql("COMMIT") == 0
    )
    connection.close()


def test_recovery_never_reuses_old_batons_and_reenables_foreign_keys(
    fault_remote, tmp_path
):
    backend, server = fault_remote
    connection = backend.connect(tmp_path / "unused")
    connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
    connection.execute("CREATE TABLE child(id INTEGER REFERENCES parent(id))")
    server.arm("SELECT 42")
    with pytest.raises(StorageUnavailable, match="OUTCOME_UNKNOWN"):
        with connection:
            connection.execute("SELECT 42")
    old_batons = {body["baton"] for body in server.requests if body["baton"]}
    boundary = len(server.requests)
    with pytest.raises(sqlite3.DatabaseError, match="^SQLITE_CONSTRAINT$"):
        with connection:
            assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            connection.execute("INSERT INTO child VALUES (999)")
    assert server.requests[boundary]["baton"] is None
    new_batons = {body["baton"] for body in server.requests[boundary:] if body["baton"]}
    assert new_batons and old_batons.isdisjoint(new_batons)
    assert connection.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 0
    connection.close()


def test_executemany_stops_at_the_failed_write_and_never_replays_its_batch(
    fault_remote, tmp_path
):
    backend, server = fault_remote
    connection = backend.connect(tmp_path / "unused")
    connection.execute("CREATE TABLE probe(value INTEGER)")
    server.arm("INSERT INTO probe")
    with pytest.raises(StorageUnavailable, match="OUTCOME_UNKNOWN"):
        connection.executemany("INSERT INTO probe VALUES (?)", [(1,), (2,)])
    assert connection.execute("SELECT value FROM probe").fetchall()[0][0] == 1
    assert server.count_sql("INSERT INTO probe") == 1
    connection.close()


def test_recovery_is_not_reported_before_successful_foreign_key_bootstrap(
    fault_remote, tmp_path, caplog
):
    backend, server = fault_remote
    connection = backend.connect(tmp_path / "unused")
    server.arm("SELECT 1")
    with pytest.raises(StorageUnavailable):
        connection.execute("SELECT 1")
    reject = True

    def transport(request):
        response = server(request)
        if reject:
            payload = response.json()
            payload["results"][0] = {
                "type": "error",
                "error": {"code": "SQLITE_ERROR", "message": "private error"},
            }
            return httpx.Response(200, json=payload)
        return response

    backend.transport = httpx.MockTransport(transport)
    caplog.set_level(logging.INFO, logger="amora.storage.remote")
    with pytest.raises(StorageUnavailable, match="SQL_FOREIGN_KEYS_UNAVAILABLE"):
        connection.execute("SELECT 2")
    assert connection._broken and server.count_sql("SELECT 2") == 1
    assert not any(r.message == "storage_connection_recovered" for r in caplog.records)
    reject = False
    assert connection.execute("SELECT 3").fetchone()[0] == 3
    assert sum(r.message == "storage_connection_recovered" for r in caplog.records) == 1
    connection.close()


@pytest.mark.parametrize("fault", ["INSERT INTO model_usage", "COMMIT"])
def test_staging_usage_failure_then_cognitive_read_journal_and_history_recover(
    fault_remote, tmp_path, monkeypatch, fault
):
    backend, server = fault_remote
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "core"))
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    core = AmoraCore.bootstrap(storage=backend)
    store = core.cognition.store
    old_client = store._conn.client
    server.arm(fault)
    try:
        before = len(server.requests)
        with pytest.raises(StorageUnavailable, match="OUTCOME_UNKNOWN"):
            store.record_usage(
                provider="fixture",
                model="fixture",
                task_class="social_dialogue",
                success=True,
            )
        assert len(server.requests) - before == (3 if fault == "COMMIT" else 2)
        assert server.count_sql("INSERT INTO model_usage") == 1
        assert store._conn._broken and not store._conn._transaction
        boundary = len(server.requests)
        assert store.get_message_by_dedupe("independent-read") is None
        assert old_client.is_closed and store._conn.client is not old_client
        assert server.requests[boundary]["baton"] is None
        entry = CognitiveJournalEntry(
            turn_id=uuid4(),
            stage=JournalStage.INPUT_OBSERVED,
            timestamp=core.clock.now(),
            dedupe_key="after-loss",
        )
        assert store.put_journal(entry) and not store.put_journal(entry)
        assert store.list_messages() == []
        assert (
            store.usage_ledger()[0]["request_count"] == 1
            if fault == "COMMIT"
            else store.usage_ledger() == []
        )
        assert server.count_sql("INSERT INTO model_usage") == 1
    finally:
        core.close()


@pytest.mark.parametrize(
    "path,read_method",
    [
        ("cognition.store", "message_count"),
        ("memory.store", "count"),
        ("conversation.store", "pending_count"),
        ("emotion.store", "event_count"),
        ("world.store", "claim_count"),
        ("autonomy.store", "decision_count"),
        ("actions.store", "plan_count"),
        ("avatar.store", "project_count"),
        ("database", "event_count"),
    ],
)
def test_every_long_lived_store_recovers_without_replacing_its_connection(
    fault_remote, tmp_path, monkeypatch, path, read_method
):
    backend, server = fault_remote
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "core"))
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    core = AmoraCore.bootstrap(storage=backend)
    store = core
    for part in path.split("."):
        store = getattr(store, part)
    connection = store._conn
    read = getattr(store, read_method)
    expected = read()
    if path == "avatar.store":
        store.put_asset("before-recovery", "image/png", b"png-persisted")
    server.arm("SELECT 42")
    try:
        with pytest.raises(StorageUnavailable, match="OUTCOME_UNKNOWN"):
            with store._lock, connection:
                connection.execute("SELECT 42")
        assert read() == expected
        assert store._conn is connection and not connection._broken
        with store._lock, connection:
            connection.execute("PRAGMA user_version=7")
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 7
        assert read() == expected
        assert server.count_sql("SELECT 42") == 1
        if path == "avatar.store":
            assert store.blobs.connection is connection
            assert store.read_asset("before-recovery")[1] == b"png-persisted"
        if path == "memory.store":
            from amora.memory import MemoryQuery
            from test_memory_v03 import important_candidate

            memory = core.memory.remember(important_candidate()).memory
            assert memory is not None
            assert any(
                hit.memory.id == memory.id
                for hit in core.memory.recall(
                    MemoryQuery(text="desenho", entity_ids={"PERSON_BK"})
                )
            )
    finally:
        core.close()
