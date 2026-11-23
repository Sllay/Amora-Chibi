"""RUN 01 candidate parity: SQLite and opt-in disposable PG18; no providers."""
from contextlib import closing
from datetime import timedelta
import os
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.events import PrivacyScope
from amora.messaging.models import MessagingPlatform, OutboundMessage
from amora.messaging.reply_jobs import ReplyJobRepository, ReplyReason
from amora.storage.database import AmoraDatabase
from amora.storage.reply_job_schema import REPLY_JOB_CANDIDATE_SQL
from amora.storage.schema import LATEST_SCHEMA_VERSION
from amora.storage.reply_job_sql import SQLITE_STATEMENTS
from amora.storage.postgres_runtime_reply_jobs import reply_job_statements
from amora.storage.postgres_runtime_registry import compiled_domain_registry
from amora.storage.postgres_runtime_connection import PostgresBackendCandidate, PostgresTargetPolicy
from amora.storage.postgres_runtime_synthetic_setup import prepare_disposable_core_v18
from amora.storage.postgres_reply_job_lab_schema import install_reply_job_lab
from amora.time import SimulatedClock


def _request(*, session="synthetic-reply-session", native="a"*20, revision=1,
             marked=True, allowed=True, test=False):
    return CognitiveRequest(
        text="Texto sintético original completo, não duplicar nem alterar.",
        speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
        session_id=session, privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=allowed,
        metadata={"surface":"platform_messaging","platform":"discord",
                  "channel_type":"dm","native_message_id_hash":native,
                  "transport_revision":revision,"reply_enqueue_requested":marked,
                  "reply_outbound_id":str(uuid4()),"test_mode":test,
                  "memory_consolidation":False},
    )


def _record(core, request):
    core.cognition.observe_input(request)
    row = core.cognition.store.get_message_by_dedupe(f"cognitive-input:{request.turn_id}")
    assert row is not None
    return str(row.id)


def _raw():
    psycopg = pytest.importorskip("psycopg")
    return psycopg.connect(host="127.0.0.1", port=5432, dbname="amora_pg_synthetic",
        user="amora_ci", password="synthetic_only", connect_timeout=5, autocommit=True)


@pytest.fixture(params=["sqlite", pytest.param("pg18", marks=pytest.mark.skipif(
    os.getenv("AMORA_PG_SYNTHETIC_LAB") != "1",
    reason="Requires disposable local PostgreSQL 18 service"))])
def lab(request, tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_MODE", "local")
    monkeypatch.setenv("AMORA_STORAGE_BACKEND", "sqlite")
    monkeypatch.delenv("AMORA_RUNTIME_SCHEMA_TARGET", raising=False)
    monkeypatch.delenv("AMORA_RUNTIME_SCHEMA_CONFIRMATION", raising=False)
    clock = SimulatedClock()
    assert LATEST_SCHEMA_VERSION == 18
    if request.param == "sqlite":
        core = AmoraCore.bootstrap(clock=clock)
        try:
            assert core.database._conn.execute(
                "SELECT name FROM sqlite_master WHERE name='messaging_reply_jobs'"
            ).fetchone() is None
            core.database._conn.executescript(REPLY_JOB_CANDIDATE_SQL)
            yield core, clock, ReplyJobRepository(core.database, clock, lease_seconds=10)
        finally:
            core.close()
        return
    with closing(_raw()) as manager:
        # Same explicit disposable schema used by existing PG18 tests.
        manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")
        try:
            prepare_disposable_core_v18(manager)
            install_reply_job_lab(manager)
            registry = compiled_domain_registry()
            candidate = reply_job_statements()
            assert not (registry.keys() & candidate.keys())
            registry.update(candidate)
            backend = PostgresBackendCandidate(_raw, registry,
                target=PostgresTargetPolicy(), max_connections=16)
            core = AmoraCore.bootstrap(clock=clock, storage=backend)
            try:
                yield core, clock, ReplyJobRepository(core.database, clock, lease_seconds=10)
            finally:
                core.close()
            assert backend.opened_connections == 0
        finally:
            manager.execute("DROP SCHEMA IF EXISTS amora_pg_v18_lab CASCADE")


def test_candidate_reviewed_sql_complete_and_not_in_default_registry():
    pg = reply_job_statements()
    assert set(pg) == {"PG_REPLY_JOB_CLAIM_LOCK"} | {
        "PG_REPLY_JOB_" + operation for operation in SQLITE_STATEMENTS}
    assert not (set(pg) & set(compiled_domain_registry()))
    for operation, sql in SQLITE_STATEMENTS.items():
        reviewed = pg["PG_REPLY_JOB_" + operation]
        assert reviewed.parameter_count == sql.count("?")
        assert reviewed.postgres_sql.count("%s") == reviewed.parameter_count


def test_candidate_recovers_record_enqueue_crash_without_old_history(lab):
    core, clock, queue = lab
    old = _request(marked=False)
    denied = _request(allowed=False)
    test = _request(test=True)
    accepted = _request()
    for request in (old, denied, test, accepted):
        _record(core, request)
    assert queue.reconcile_missing() == 1
    assert queue.reconcile_missing() == 0
    for request in (old, denied, test):
        assert queue.state(request.turn_id) is None
    assert queue.state(accepted.turn_id)["status"] == "pending"
    if core.database.backend.capabilities.name == "sqlite":
        columns = {row[1] for row in core.database._conn.execute(
            "PRAGMA table_info(messaging_reply_jobs)"
        )}
    else:
        with closing(_raw()) as raw:
            columns = {row[0] for row in raw.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_schema='amora_pg_v18_lab' AND table_name='messaging_reply_jobs'"
            )}
    assert not columns.intersection({"text","payload","metadata_json","image","prompt"})


def test_candidate_dedupes_and_recovers_expired_lease_across_connections(lab):
    core, clock, queue = lab
    request = _request()
    input_id = _record(core, request)
    assert queue.enqueue(input_id)
    assert not queue.enqueue(input_id)
    first = queue.claim()
    assert first and first.attempts == 1
    assert queue.claim() is None
    clock.advance(timedelta(seconds=11))
    reopened = AmoraDatabase(core.database.path, backend=core.database.backend)
    try:
        other = ReplyJobRepository(reopened, clock, lease_seconds=10)
        second = other.claim()
        assert second and second.turn_id == first.turn_id
        assert second.input_id == input_id and second.attempts == 2
        assert second.token != first.token
        assert not queue.renew(first)
        assert not queue.defer(first, ReplyReason.PROVIDER_TEMPORARY, delay_seconds=30)
        assert other.defer(second, ReplyReason.PROVIDER_TEMPORARY, delay_seconds=120)
        assert other.claim() is None
        clock.advance(timedelta(seconds=119))
        assert other.claim() is None
        clock.advance(timedelta(seconds=1))
        third = other.claim()
        assert third and third.attempts == 3
        assert third.input_id == input_id
        assert core.cognition.store.get_message_by_dedupe(
            f"cognitive-input:{request.turn_id}"
        ).raw_text == request.text
    finally:
        reopened.close()


def test_candidate_fifo_and_cross_session_fairness(lab):
    core, clock, queue = lab
    first = _request()
    assert queue.enqueue(_record(core, first))
    claim = queue.claim()
    assert claim
    assert queue.defer(claim, ReplyReason.PROVIDER_BACKOFF, delay_seconds=120)
    clock.advance(timedelta(seconds=1))
    later = _request(native="b"*20)
    other = _request(session="other-synthetic-session", native="c"*20)
    assert queue.enqueue(_record(core, later))
    assert queue.enqueue(_record(core, other))
    independent = queue.claim()
    assert independent and independent.turn_id == str(other.turn_id)
    assert queue.block(independent, ReplyReason.CONFIGURATION_BLOCKED)
    assert queue.claim() is None
    clock.advance(timedelta(seconds=119))
    resumed = queue.claim()
    assert resumed and resumed.turn_id == str(first.turn_id)
    assert queue.block(resumed, ReplyReason.CONTEXT_BLOCKED)
    later_claim = queue.claim()
    assert later_claim and later_claim.turn_id == str(later.turn_id)
    assert queue.resume_blocked(first.turn_id)


def test_candidate_concurrent_claim_has_one_winner(lab):
    core, clock, queue = lab
    request = _request()
    assert queue.enqueue(_record(core, request))
    clock.advance(timedelta(seconds=1))
    independent = _request(session="second-concurrent-session", native="b"*20)
    assert queue.enqueue(_record(core, independent))
    second = AmoraDatabase(core.database.path, backend=core.database.backend)
    try:
        other = ReplyJobRepository(second, clock, lease_seconds=10)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(queue.claim)
            b = pool.submit(other.claim)
            claims = [claim for claim in (a.result(), b.result()) if claim]
        assert len(claims) == 1
        assert claims[0].turn_id == str(request.turn_id)
    finally:
        second.close()


def test_candidate_renew_and_invalidation_reject_stale_results(lab):
    core, clock, queue = lab
    request = _request()
    assert queue.enqueue(_record(core, request))
    claim = queue.claim()
    assert claim
    clock.advance(timedelta(seconds=8))
    assert queue.renew(claim)
    clock.advance(timedelta(seconds=3))
    assert queue.claim() is None
    core.messaging.reflections.invalidate_source(
        request.session_id, request.metadata["native_message_id_hash"], through_revision=1)
    assert not queue.renew(claim)
    assert not queue.defer(claim, ReplyReason.PROVIDER_TEMPORARY, delay_seconds=10)
    assert queue.cancel_invalidated() == 1
    assert queue.state(request.turn_id)["status"] == "cancelled"
    assert not queue.enqueue(_record(core, request))
    newer = _request(revision=2)
    assert queue.enqueue(_record(core, newer))
    assert queue.claim().turn_id == str(newer.turn_id)


def test_candidate_ready_is_not_sent_and_requires_expected_outbox(lab):
    core, clock, queue = lab
    request = _request()
    assert queue.enqueue(_record(core, request))
    claim = queue.claim()
    assert claim
    expected = request.metadata["reply_outbound_id"]
    assert not queue.mark_ready(claim, expected)
    assert not queue.mark_ready(claim, str(uuid4()))
    # Synthetic outbox only: the shared generation/outbox transaction fence is
    # still a pending integration task, not proven by this repository test.
    msg = OutboundMessage(id=expected, platform=MessagingPlatform.DISCORD,
        account_scope="synthetic", conversation_id="synthetic-conversation",
        target_entity_id="PERSON_BK", text="Resposta sintética.", created_at=clock.now())
    assert core.messaging.store.put_outbound(msg)
    assert queue.mark_ready(claim, expected)
    assert queue.state(request.turn_id)["status"] == "ready"
    assert not queue.observe_sent(request.turn_id)
    assert not queue.defer(claim, ReplyReason.PROVIDER_TEMPORARY, delay_seconds=10)
    claimed = core.messaging.store.claim_outbound(
        platform=MessagingPlatform.DISCORD,account_scope="synthetic")
    assert len(claimed) == 1
    assert core.messaging.store.ack_outbound(
        msg.id,sent=True,external_message_id="synthetic-ack")
    assert queue.observe_sent(request.turn_id)
    assert queue.state(request.turn_id)["status"] == "sent"
    assert not queue.observe_sent(request.turn_id)


def test_candidate_revoked_job_cannot_be_resumed(lab):
    core, clock, queue = lab
    request = _request()
    assert queue.enqueue(_record(core, request))
    claim = queue.claim()
    assert queue.block(claim, ReplyReason.AUTHORIZATION_REVOKED)
    assert not queue.resume_blocked(request.turn_id)
    assert queue.claim() is None


def test_candidate_same_session_never_runs_twice_even_with_spare_capacity(lab):
    core, clock, queue = lab
    multi = ReplyJobRepository(core.database, clock, lease_seconds=10, max_running=2)
    first = _request()
    assert multi.enqueue(_record(core, first))
    older = multi.claim()
    assert older
    assert multi.block(older, ReplyReason.CONTEXT_BLOCKED)
    clock.advance(timedelta(seconds=1))
    later = _request(native="b"*20)
    assert multi.enqueue(_record(core, later))
    active = multi.claim()
    assert active and active.turn_id == str(later.turn_id)
    assert multi.resume_blocked(first.turn_id)
    assert multi.claim() is None
    other = _request(session="unrelated-session", native="c"*20)
    assert multi.enqueue(_record(core, other))
    independent = multi.claim()
    assert independent and independent.turn_id == str(other.turn_id)


def test_candidate_rejects_malformed_revision_and_non_dm_marker(lab):
    core, clock, queue = lab
    for key, value in (("transport_revision", "1"), ("transport_revision", 0),
                       ("transport_revision", 1.5), ("native_message_id_hash", "z"*20),
                       ("channel_type", "group"), ("platform", "web")):
        req = _request()
        req.metadata[key] = value
        source = _record(core, req)
        assert not queue.enqueue(source)
    assert queue.reconcile_missing() == 0
