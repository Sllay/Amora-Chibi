"""Hrana mock backed by real SQLite, not a fake success response.

Exercises actual schema, constraints, FTS, transactions and every store through
HTTP serialization. It does not certify a Turso server or its network behavior.
"""

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import httpx
import pytest
from amora.app import AmoraCore
from amora.api import create_api
from amora.events import AmoraEvent
from amora.storage import AmoraDatabase
from amora.storage.remote import LibSQLBackend, StorageUnavailable, _decode, _encode
from fastapi.testclient import TestClient


class HranaSQLite:
    def __init__(
        self, path, *, close_after_transaction=True, primary_constraint_codes=False
    ):
        self.primary_constraint_codes = primary_constraint_codes
        self.close_after_transaction = close_after_transaction
        self.path, self.streams = path, {}
        self.fail = False
        self.calls = []
        self.closed_transaction_streams = 0

    def __call__(self, request):
        if self.fail:
            raise httpx.ReadTimeout(
                "transport interrupted; no secret should be returned"
            )
        body = json.loads(request.content)
        baton = body["baton"] or str(uuid4())
        connection = self.streams.get(baton)
        if connection is None:
            connection = sqlite3.connect(
                self.path, check_same_thread=False, isolation_level=None
            )
            self.streams[baton] = connection
        results = []
        for operation in body["requests"]:
            if operation["type"] == "close":
                connection.close()
                del self.streams[baton]
                baton = None
                results.append({"type": "ok", "response": {"type": "close"}})
                continue
            statement = operation["stmt"]
            self.calls.append(statement["sql"])
            try:
                cursor = connection.execute(
                    statement["sql"], tuple(_decode(v) for v in statement["args"])
                )
                columns = [{"name": c[0]} for c in (cursor.description or [])]
                rows = [[_encode(v) for v in row] for row in cursor.fetchall()]
                results.append(
                    {
                        "type": "ok",
                        "response": {
                            "type": "execute",
                            "result": {
                                "cols": columns,
                                "rows": rows,
                                "affected_row_count": max(0, cursor.rowcount),
                            },
                        },
                    }
                )
            except sqlite3.Error as exc:
                code = exc.sqlite_errorname
                if self.primary_constraint_codes and code.startswith(
                    "SQLITE_CONSTRAINT"
                ):
                    code = "SQLITE_CONSTRAINT"
                results.append(
                    {
                        "type": "error",
                        "error": {"code": code, "message": str(exc)},
                    }
                )
        if (
            baton is not None
            and self.close_after_transaction
            and not connection.in_transaction
            and any(
                operation["type"] == "execute"
                and operation["stmt"]["sql"].strip().rstrip(";").strip().upper()
                in {"COMMIT", "ROLLBACK"}
                and result["type"] == "ok"
                for operation, result in zip(body["requests"], results)
            )
        ):
            connection.close()
            del self.streams[baton]
            baton = None
            self.closed_transaction_streams += 1
        return httpx.Response(
            200, json={"baton": baton, "base_url": None, "results": results}
        )


@pytest.fixture
def remote(tmp_path):
    server = HranaSQLite(tmp_path / "server.db")
    backend = LibSQLBackend(
        "https://database.example", "test-only", transport=httpx.MockTransport(server)
    )
    return backend, server


def test_remote_migration_constraints_rollback_and_restart(remote, tmp_path):
    backend, server = remote
    db = AmoraDatabase(tmp_path / "unused.db", backend=backend)
    assert db.migrate() == 11
    assert server.closed_transaction_streams > 0
    assert db.migrate() == 0
    event = AmoraEvent(event_type="probe", source="test", dedupe_key="persistent")
    assert db.append_event(event)
    assert not db.append_event(event)
    with pytest.raises(sqlite3.DatabaseError):
        with db._conn:
            db._conn.execute("INSERT INTO avatar_asset_blobs VALUES ('bad', X'')")
    assert (
        db._conn.execute("SELECT COUNT(*) FROM avatar_asset_blobs").fetchone()[0] == 0
    )
    with pytest.raises(sqlite3.DatabaseError):
        with db._conn:
            db._conn.execute("INSERT INTO aliases VALUES ('missing', 'NOT_AN_ENTITY')")
    db.close()
    db = AmoraDatabase(tmp_path / "unused.db", backend=backend)
    assert db.schema_version() == 11
    assert not db.append_event(event)
    db.close()
    assert not server.streams
    assert not (tmp_path / "unused.db").exists()
    assert not any("journal_mode" in q or "synchronous" in q for q in server.calls)


def test_remote_core_memory_web_and_snapshot_with_sql_blobs(
    remote, tmp_path, monkeypatch
):
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
            response = client.post(
                "/v1/web/utterance", json={"text": "Amora, oi."}, headers=headers
            )
            assert response.status_code == 200
            assert response.json()["reply_queued"]["device_id"] == "WEB_SAFARI"
            assert client.get("/ready").status_code == 200
        backup = core.create_backup("remote.db")
        with sqlite3.connect(backup) as restored:
            assert restored.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
            assert (
                restored.execute("SELECT body FROM avatar_asset_blobs").fetchone()[0]
                == b"png-data"
            )
        from amora.storage.recovery import restore_empty_remote

        restored_server = HranaSQLite(tmp_path / "restored-server.db")
        restored_backend = LibSQLBackend(
            "https://database.example",
            "test-only",
            transport=httpx.MockTransport(restored_server),
        )
        restore_empty_remote(restored_backend, backup)
        with pytest.raises(ValueError, match="not empty"):
            restore_empty_remote(restored_backend, backup)
        check = AmoraDatabase(tmp_path / "unused-restored.db", backend=restored_backend)
        assert check.integrity_check() == "ok"
        assert check.schema_version() == 11
        check.close()
        core.close()
        # New compute home, same remote state and service identity.
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


def test_outage_is_not_dedupe_or_empty_local_database(remote, tmp_path):
    backend, server = remote
    db = AmoraDatabase(tmp_path / "unused.db", backend=backend)
    db.migrate()
    server.fail = True
    with pytest.raises(StorageUnavailable, match="OUTCOME_UNKNOWN"):
        db.append_event(AmoraEvent(event_type="x", source="test"))
    db.close()
    assert not (tmp_path / "unused.db").exists()


def test_config_validation_and_script_parser():
    from amora.storage.remote import statements

    with pytest.raises(ValueError):
        LibSQLBackend("http://database.example", "secret")
    with pytest.raises(ValueError):
        LibSQLBackend("https://user:secret@database.example", "secret")
    assert (
        len(list(statements("CREATE TABLE x(t); INSERT INTO x VALUES ('a;b');"))) == 2
    )


@pytest.mark.parametrize(
    "module,name",
    [
        ("test_memory_api_v03", "test_memory_roundtrip_through_local_api"),
        ("test_world_api_v04", "test_belief_revision_visible_in_state_and_history"),
        (
            "test_world_api_v04",
            "test_self_and_relationship_history_are_readable_when_authenticated",
        ),
        (
            "test_emotion_api_v05",
            "test_appraisal_route_updates_directed_affect_mood_and_bid",
        ),
        (
            "test_autonomy_api_v06",
            "test_drive_adjustment_and_cycle_are_idempotent_over_api",
        ),
        ("test_actions_api_v07", "test_plan_execute_and_replay_over_api"),
        (
            "test_web_safari_v101",
            "test_web_delivery_ack_is_only_recorded_after_explicit_playback_ack",
        ),
        (
            "test_web_safari_v101",
            "test_web_manual_cancel_marks_claimed_utterance_cancelled",
        ),
    ],
)
def test_existing_domain_contract_over_remote_sql(
    remote, tmp_path, monkeypatch, module, name
):
    import importlib

    backend, server = remote
    monkeypatch.setattr("amora.app.backend_from_environment", lambda: backend)
    getattr(importlib.import_module(module), name)(tmp_path, monkeypatch)
    assert not server.streams


def test_local_claim_is_atomic_across_store_instances(tmp_path, monkeypatch):
    import asyncio
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from amora.conversation import ConversationStore, UtteranceKind
    from amora.events import PrivacyScope

    monkeypatch.setenv("AMORA_HOME", str(tmp_path))
    core = AmoraCore.bootstrap()
    other = ConversationStore(core.paths.database, core.clock)
    try:
        asyncio.run(
            core.conversation.enqueue_text(
                "Oi.",
                target_id="PERSON_BK",
                kind=UtteranceKind.DIALOGUE,
                priority=70,
                privacy_scope=PrivacyScope.PERSONAL,
                source_event_id=None,
                dedupe_key="claim-race",
                device_id="WEB_SAFARI",
            )
        )
        barrier = threading.Barrier(2)

        def claim(store):
            barrier.wait()
            return store.claim_next("WEB_SAFARI")

        with ThreadPoolExecutor(2) as pool:
            answers = list(pool.map(claim, [core.conversation.store, other]))
        assert sum(answer is not None for answer in answers) == 1
    finally:
        other.close()
        core.close()


def test_transaction_cannot_silently_continue_after_lost_baton(tmp_path):
    backend = LibSQLBackend(
        "https://database.example",
        "test-only",
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "baton": None,
                    "base_url": None,
                    "results": [
                        {
                            "type": "ok",
                            "response": {
                                "type": "execute",
                                "result": {
                                    "cols": [],
                                    "rows": [],
                                    "affected_row_count": 0,
                                },
                            },
                        }
                        for _ in json.loads(request.content)["requests"]
                    ],
                },
            )
        ),
    )
    connection = backend.connect(tmp_path / "unused")
    try:
        with pytest.raises(StorageUnavailable):
            with connection:
                pytest.fail("lost transaction stream must be rejected before writes")
    finally:
        connection.close()


@pytest.mark.parametrize("close_after_transaction", [True, False])
def test_commit_accepts_closed_or_retained_stream(tmp_path, close_after_transaction):
    server = HranaSQLite(
        tmp_path / "server.db", close_after_transaction=close_after_transaction
    )
    replies = []

    def transport(request):
        response = server(request)
        replies.append((json.loads(request.content), response.json()))
        return response

    backend = LibSQLBackend(
        "https://database.example",
        "test-only",
        transport=httpx.MockTransport(transport),
    )
    connection = backend.connect(tmp_path / "unused")
    try:
        with connection:
            connection.execute("CREATE TABLE probe(value TEXT)")
            connection.execute("INSERT INTO probe VALUES (?)", ("persisted",))
        commit_request, commit_response = next(
            (body, reply)
            for body, reply in replies
            if any(op.get("stmt", {}).get("sql") == "COMMIT" for op in body["requests"])
        )
        assert [op["type"] for op in commit_request["requests"]] == ["execute"]
        assert (commit_response["baton"] is None) == close_after_transaction
        assert not connection._broken
    finally:
        connection.close()
    again = backend.connect(tmp_path / "unused")
    try:
        assert again.execute("SELECT value FROM probe").fetchone()[0] == "persisted"
    finally:
        again.close()
    assert not server.streams


def test_statement_error_rolls_back_with_null_baton(remote, tmp_path):
    backend, server = remote
    connection = backend.connect(tmp_path / "unused")
    try:
        connection.execute("CREATE TABLE probe(value TEXT UNIQUE)")
        with pytest.raises(sqlite3.IntegrityError, match="SQLITE_CONSTRAINT_UNIQUE"):
            with connection:
                connection.execute("INSERT INTO probe VALUES ('duplicate')")
                connection.execute("INSERT INTO probe VALUES ('duplicate')")
        assert server.calls[-1] == "ROLLBACK"
        assert server.closed_transaction_streams == 1
        assert not server.streams
        assert not connection._broken
        assert connection.execute("SELECT COUNT(*) FROM probe").fetchone()[0] == 0
    finally:
        connection.close()


@pytest.mark.parametrize("sql", ["SELECT 1", "CREATE TABLE probe(value TEXT)"])
def test_baton_lost_during_statement_invalidates_transaction(remote, tmp_path, sql):
    backend, server = remote

    def transport(request):
        response = server(request)
        if any(
            op.get("stmt", {}).get("sql") == sql
            for op in json.loads(request.content)["requests"]
        ):
            payload = response.json()
            baton = payload["baton"]
            server.streams.pop(baton).close()
            payload["baton"] = None
            return httpx.Response(200, json=payload)
        return response

    backend.transport = httpx.MockTransport(transport)
    connection = backend.connect(tmp_path / "unused")
    try:
        with pytest.raises(
            StorageUnavailable, match="SQL_TRANSPORT_FAILED_OUTCOME_UNKNOWN"
        ) as caught:
            with connection:
                connection.execute(sql)
        assert str(caught.value.__cause__) == "SQL_TRANSACTION_STREAM_LOST"
        assert connection._broken
        assert "COMMIT" not in server.calls
        assert not connection._transaction
        # The failed transaction stays failed; an independent read may recover.
        assert connection.execute("SELECT 2").fetchone()[0] == 2
    finally:
        connection.close()
    assert not server.streams


@pytest.mark.parametrize("failure", ["http", "timeout"])
def test_commit_transport_failure_is_unknown_and_never_retried(
    remote, tmp_path, failure
):
    backend, server = remote
    commits = []

    def transport(request):
        body = json.loads(request.content)
        is_commit = any(
            op.get("stmt", {}).get("sql") == "COMMIT" for op in body["requests"]
        )
        response = server(request)  # Commit may succeed before its response is lost.
        if is_commit:
            commits.append(body)
            if failure == "timeout":
                raise httpx.ReadTimeout("response lost", request=request)
            return httpx.Response(503)
        return response

    backend.transport = httpx.MockTransport(transport)
    connection = backend.connect(tmp_path / "unused")
    try:
        with pytest.raises(
            StorageUnavailable, match="^SQL_TRANSPORT_FAILED_OUTCOME_UNKNOWN$"
        ):
            with connection:
                connection.execute("CREATE TABLE probe(value TEXT)")
        assert len(commits) == 1
        assert connection._broken
        assert not connection._transaction
        assert connection.execute("SELECT 1").fetchone()[0] == 1
        assert len(commits) == 1
    finally:
        connection.close()
    assert not server.streams


@pytest.mark.parametrize("terminator", ["COMMIT", "ROLLBACK"])
def test_terminator_sql_error_with_null_baton_is_not_success(
    remote, tmp_path, terminator
):
    backend, server = remote

    def transport(request):
        body = json.loads(request.content)
        if any(op.get("stmt", {}).get("sql") == terminator for op in body["requests"]):
            server.streams.pop(body["baton"]).close()
            return httpx.Response(
                200,
                json={
                    "baton": None,
                    "results": [
                        {
                            "type": "error",
                            "error": {
                                "code": "SQLITE_BUSY",
                                "message": "private SQL values",
                            },
                        }
                    ],
                },
            )
        return server(request)

    backend.transport = httpx.MockTransport(transport)
    connection = backend.connect(tmp_path / "unused")
    try:
        with pytest.raises(StorageUnavailable, match="^SQLITE_BUSY$"):
            with connection:
                connection.execute("CREATE TABLE probe(value TEXT)")
                if terminator == "ROLLBACK":
                    raise ValueError("trigger rollback")
        assert connection._broken
    finally:
        connection.close()
    assert not server.streams


def test_null_baton_does_not_hide_malformed_terminator_result(remote, tmp_path):
    backend, server = remote

    def transport(request):
        response = server(request)
        if any(
            op.get("stmt", {}).get("sql") == "COMMIT"
            for op in json.loads(request.content)["requests"]
        ):
            payload = response.json()
            assert payload["baton"] is None
            payload["results"][0]["response"]["type"] = "close"
            return httpx.Response(200, json=payload)
        return response

    backend.transport = httpx.MockTransport(transport)
    connection = backend.connect(tmp_path / "unused")
    try:
        with pytest.raises(StorageUnavailable, match="^SQL_INVALID_RESPONSE$"):
            with connection:
                connection.execute("CREATE TABLE probe(value TEXT)")
        assert connection._broken
    finally:
        connection.close()
    assert not server.streams


@pytest.mark.parametrize("primary", [False, True])
def test_constraint_classification_never_treats_foreign_key_or_not_null_as_dedupe(
    tmp_path, primary
):
    from amora.storage.backend import is_duplicate_error

    server = HranaSQLite(tmp_path / "server.db", primary_constraint_codes=primary)
    backend = LibSQLBackend(
        "https://database.example", "test-only", transport=httpx.MockTransport(server)
    )
    connection = backend.connect(tmp_path / "unused")
    try:
        connection.execute("CREATE TABLE parent(id INTEGER PRIMARY KEY)")
        connection.execute(
            "CREATE TABLE child(value TEXT NOT NULL UNIQUE, parent_id INTEGER REFERENCES parent(id))"
        )
        with connection:
            connection.execute("INSERT INTO child(value) VALUES ('duplicate')")
        with pytest.raises(sqlite3.DatabaseError) as unique:
            with connection:
                connection.execute("INSERT INTO child(value) VALUES ('duplicate')")
        assert is_duplicate_error(unique.value) is (not primary)
        for sql in [
            "INSERT INTO child(value) VALUES (NULL)",
            "INSERT INTO child VALUES ('foreign', 99)",
        ]:
            with pytest.raises(sqlite3.DatabaseError) as invalid:
                with connection:
                    connection.execute(sql)
            assert not is_duplicate_error(invalid.value)
        assert connection.execute("SELECT COUNT(*) FROM child").fetchone()[0] == 1
    finally:
        connection.close()
    assert not server.streams
