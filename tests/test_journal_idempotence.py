"""Journal no-op is an SQL conflict target, independent of Hrana diagnostics."""

import json
import sqlite3
from uuid import uuid4

import httpx
import pytest

from amora.cognition.models import CognitiveJournalEntry, JournalStage
from amora.cognition.store import CognitiveStore
from amora.storage import AmoraDatabase
from amora.storage.backend import SQLiteBackend
from amora.storage.remote import LibSQLBackend, StorageUnavailable
from amora.time import SystemClock
from test_remote_storage import HranaSQLite


@pytest.fixture(params=["sqlite", "hrana"])
def journal(request, tmp_path):
    server = HranaSQLite(tmp_path / "remote.db", primary_constraint_codes=True)
    replies = []
    faults = {}

    def transport(req):
        body = json.loads(req.content)
        reply = server(req)
        payload = reply.json()
        for result in payload["results"]:
            if result["type"] == "error":
                # Model a server that never reveals a usable constraint message.
                result["error"]["message"] = "opaque server diagnostic"
        journal_indexes = [
            index
            for index, op in enumerate(body["requests"])
            if "INSERT INTO cognitive_journal" in op.get("stmt", {}).get("sql", "")
        ]
        is_journal = bool(journal_indexes)
        if is_journal and "rowcount" in faults:
            for index in journal_indexes:
                payload["results"][index]["response"]["result"]["affected_row_count"] = faults[
                    "rowcount"
                ]
        replies.append((body, payload))
        if faults.get("journal_response") and is_journal:
            # The server already executed the autocommit statement. Losing this
            # response makes the outcome ambiguous; the client must never replay it.
            faults["journal_response"] = False
            raise httpx.ReadTimeout("journal response lost", request=req)
        if faults.get("commit") and any(
            op.get("stmt", {}).get("sql") == "COMMIT" for op in body["requests"]
        ):
            raise httpx.ReadTimeout("commit response lost", request=req)
        return httpx.Response(200, json=payload)

    backend = (
        SQLiteBackend()
        if request.param == "sqlite"
        else LibSQLBackend(
            "https://database.example",
            "test-only",
            transport=httpx.MockTransport(transport),
        )
    )
    path = tmp_path / "local.db"
    db = AmoraDatabase(path, backend=backend)
    db.migrate()
    db.close()
    store = CognitiveStore(path, SystemClock(), backend=backend)
    replies.clear()
    server.closed_transaction_streams = 0
    try:
        yield store, server, replies, faults
    finally:
        store.close()
        assert not server.streams


def entry(**updates):
    item = CognitiveJournalEntry(
        turn_id=uuid4(),
        stage=JournalStage.INPUT_OBSERVED,
        timestamp=SystemClock().now(),
        dedupe_key="same-journal",
    )
    return item.model_copy(update=updates)


def _statement_result(body, reply, needle: str):
    for index, op in enumerate(body["requests"]):
        if needle in op.get("stmt", {}).get("sql", ""):
            return reply["results"][index]
    raise AssertionError(f"statement not found: {needle}")


def test_new_insert_and_same_dedupe_have_exact_boolean_results(journal):
    store, server, replies, _ = journal
    first = entry()
    assert store.put_journal(first) is True
    assert store.put_journal(first.model_copy(update={"id": uuid4()})) is False
    assert store.journal_count() == 1
    assert store.put_journal(entry(dedupe_key=None)) is True
    assert store.put_journal(entry(dedupe_key=None)) is True
    if store.backend.capabilities.remote:
        results = [
            _statement_result(body, reply, "INSERT INTO cognitive_journal")["response"]["result"]["affected_row_count"]
            for body, reply in replies
            if any(
                "INSERT INTO cognitive_journal" in op.get("stmt", {}).get("sql", "")
                for op in body["requests"]
            )
        ]
        assert results == [1, 0, 1, 1]
        assert all(
            result["type"] == "ok"
            for _, reply in replies
            for result in reply["results"]
        )
        assert not server.streams


def test_unexpected_primary_key_conflict_is_not_a_dedupe(journal):
    store, _, _, _ = journal
    first = entry()
    assert store.put_journal(first)
    with pytest.raises(sqlite3.DatabaseError):
        store.put_journal(first.model_copy(update={"dedupe_key": "different-key"}))
    assert store.journal_count() == 1


def test_not_null_constraint_is_not_a_dedupe(journal):
    store, _, _, _ = journal
    with pytest.raises(sqlite3.DatabaseError):
        store.put_journal(entry(source=None))
    assert store.journal_count() == 0


@pytest.mark.parametrize("constraint", ["check", "foreign_key"])
def test_other_schema_constraints_are_not_swallowed(journal, constraint):
    store, _, _, _ = journal
    # Add constraints only to this disposable test DB; production schema unchanged.
    ddl = store._conn.execute(
        "SELECT sql FROM sqlite_schema WHERE name='cognitive_journal'"
    ).fetchone()[0]
    store._conn.execute("DROP TABLE cognitive_journal")
    if constraint == "check":
        ddl = ddl.replace(
            "source TEXT NOT NULL", "source TEXT NOT NULL CHECK(source != 'forbidden')"
        )
    else:
        store._conn.execute("CREATE TABLE allowed_sources(id TEXT PRIMARY KEY)")
        ddl = ddl.replace(
            "source TEXT NOT NULL",
            "source TEXT NOT NULL REFERENCES allowed_sources(id)",
        )
    store._conn.execute(ddl)
    with pytest.raises(sqlite3.DatabaseError):
        store.put_journal(entry(source="forbidden"))
    assert store.journal_count() == 0


@pytest.mark.parametrize("journal", ["hrana"], indirect=True)
def test_journal_autocommit_response_unknown_is_not_retried_or_reported_success(journal):
    store, server, replies, faults = journal
    faults["journal_response"] = True
    with pytest.raises(
        StorageUnavailable, match="^SQL_TRANSPORT_FAILED_OUTCOME_UNKNOWN$"
    ):
        store.put_journal(entry())
    inserts = [
        body
        for body, _ in replies
        if any(
            "INSERT INTO cognitive_journal" in op.get("stmt", {}).get("sql", "")
            for op in body["requests"]
        )
    ]
    assert len(inserts) == 1
    assert server.count_sql("INSERT INTO cognitive_journal") == 1
    assert store._conn._broken
    # A later independent read may recover the connection, but must not replay
    # the ambiguous write. The mock server executed it before the response loss.
    assert store.journal_count() == 1
    assert server.count_sql("INSERT INTO cognitive_journal") == 1


@pytest.mark.parametrize("journal", ["hrana"], indirect=True)
def test_transport_failure_does_not_return_a_dedupe(journal):
    store, server, _, _ = journal
    server.fail = True
    with pytest.raises(StorageUnavailable, match="OUTCOME_UNKNOWN"):
        store.put_journal(entry())


@pytest.mark.parametrize("rowcount", [None, -1, True, "1", 2])
@pytest.mark.parametrize("journal", ["hrana"], indirect=True)
def test_invalid_affected_count_cannot_report_success_or_dedupe(journal, rowcount):
    store, _, _, faults = journal
    faults["rowcount"] = rowcount
    with pytest.raises(sqlite3.DatabaseError):
        store.put_journal(entry())
    assert store.journal_count() == 0
