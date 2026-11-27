"""RUN 01 candidate parity: SQLite and opt-in disposable PG18; no providers."""
from contextlib import closing
import asyncio
from datetime import timedelta
import os
from uuid import uuid4
from concurrent.futures import ThreadPoolExecutor

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.events import PrivacyScope
from amora.messaging.models import (AttentionDecision, MessagingPlatform, OutboundMessage,
    RoutingAction, InboundMessage, ChannelType, ExternalActorRef, TransportInvalidation)
from amora.messaging.generation_lifecycle import InboundGenerationBusy, InboundGenerationLost
from amora.messaging.store import MessagingStore
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
from test_messaging_lifecycle_stability import CountingStructuredProvider
from test_platform_messaging_step2 import _bind_bk


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
            # Disposable candidate registry must be assembled BEFORE bootstrap.
            # The production default registry remains unchanged.
            from amora.storage.postgres_runtime_realtime import realtime_statements
            extra=realtime_statements()
            assert not (registry.keys() & extra.keys())
            registry.update(extra)
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
    # Use the existing outbound fence with the candidate claim's token.
    # The serving dispatcher and atomic finalization remain pending.
    msg = OutboundMessage(id=expected, platform=MessagingPlatform.DISCORD,
        account_scope="synthetic", conversation_id="synthetic-conversation",
        target_entity_id="PERSON_BK", text="Resposta sintética.", created_at=clock.now())
    assert core.messaging.store.put_outbound(
        msg, generation_lease=(claim.turn_id, claim.token))
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


def test_candidate_reuses_prepared_response_and_fences_expired_owner(lab):
    core, clock, queue = lab
    request = _request()
    assert queue.enqueue(_record(core, request))
    old = queue.claim()
    assert old
    outbound = OutboundMessage(
        id=request.metadata["reply_outbound_id"], platform=MessagingPlatform.DISCORD,
        account_scope="synthetic", conversation_id="synthetic-conversation",
        target_entity_id="PERSON_BK", text="Resposta preparada antes do crash.",
        created_at=clock.now())
    decision = AttentionDecision(action=RoutingAction.RESPOND,
        speaker_entity_id="PERSON_BK", speaker_actor_ref="synthetic-actor")
    assert core.messaging.generations.stage(old.turn_id, old.token,
        outbound, decision, ["SYNTHETIC_CHECKPOINT"])
    clock.advance(timedelta(seconds=11))
    assert not core.messaging.generations.stage(old.turn_id, old.token,
        outbound, decision, ["STALE_CHECKPOINT"])
    new = queue.claim()
    assert new and new.turn_id == old.turn_id and new.token != old.token
    prepared = core.messaging.generations.prepared(new.turn_id, new.token)
    assert prepared is not None and prepared[0] == outbound
    assert not queue.renew(old)
    with pytest.raises(InboundGenerationLost):
        core.messaging.store.put_outbound(outbound,
            generation_lease=(old.turn_id, old.token))
    assert core.messaging.store.get_outbound(outbound.id) is None
    assert queue.renew(new)
    assert core.messaging.store.put_outbound(prepared[0],
        generation_lease=(new.turn_id, new.token))
    assert queue.mark_ready(new, str(outbound.id))
    assert core.messaging.generations.prepared(new.turn_id, new.token) is None
    assert not core.messaging.generations.stage(new.turn_id, new.token,
        outbound, decision, ["AFTER_READY"])
    assert not queue.claim()


def test_candidate_respects_existing_generation_lease_and_retains_checkpoint_on_retry(lab):
    core, clock, queue = lab
    request = _request()
    assert queue.enqueue(_record(core, request))
    # Legacy ownership may exist during a staged rollout; the queue must not
    # override a valid owner or increment its own attempt counter in that case.
    assert core.messaging.generations.claim(
        str(request.turn_id), request.session_id,
        request.metadata["native_message_id_hash"], 1, "legacy-owner", seconds=30)
    assert queue.claim() is None
    assert queue.state(request.turn_id)["attempts"] == 0
    assert core.messaging.generations.release(request.turn_id, "legacy-owner")
    first = queue.claim()
    assert first and first.attempts == 1
    outbound = OutboundMessage(
        id=request.metadata["reply_outbound_id"], platform=MessagingPlatform.DISCORD,
        conversation_id="synthetic-conversation", text="Checkpoint preservado.",
        created_at=clock.now())
    decision = AttentionDecision(action=RoutingAction.RESPOND,
        speaker_actor_ref="synthetic-actor")
    assert core.messaging.generations.stage(first.turn_id, first.token,
        outbound, decision, [])
    assert queue.defer(first, ReplyReason.STORAGE_TEMPORARY, delay_seconds=10)
    assert not core.messaging.generations.stage(first.turn_id, first.token,
        outbound, decision, [])
    clock.advance(timedelta(seconds=10))
    second = queue.claim()
    assert second and second.attempts == 2
    assert core.messaging.generations.prepared(second.turn_id, second.token)[0] == outbound


def _dm(core, message_id="durable-msg-1", *, text="  Amora, me explica o que é uma árvore.  ",
        revision=1, native=None, actor="111111111111111111"):
    return InboundMessage(platform=MessagingPlatform.DISCORD,
        account_scope="primary", conversation_id="durable-dm",
        message_id=message_id, native_message_id=native or message_id,
        transport_revision=revision, channel_type=ChannelType.DM,
        sender_identifiers=[ExternalActorRef(identifier_type="discord_user_id",
            external_actor_id=actor)], text=text, timestamp=core.clock.now(),
        remote_processing_allowed=True)


async def _start_runtime(core):
    _bind_bk(core)
    runtime = core.messaging.configure_durable_replies()
    await runtime.start(background=False)
    return runtime


async def _stop_runtime(core):
    await core.messaging.stop_inflight()
    await core.messaging.stop_storage_lane()


@pytest.mark.asyncio
async def test_integrated_durable_ingress_retry_without_error_history_then_delivery(lab):
    core, clock, _ = lab
    runtime = await _start_runtime(core)
    provider = CountingStructuredProvider(name="durable-test", delay=0.2)
    core.cognition.register_provider(provider)
    service = core.messaging
    service.provider_attempt_budget_seconds = 0.02
    service.provider_total_budget_seconds = 0.08
    msg = _dm(core)
    turn = service._turn_id(msg)
    try:
        accepted = await asyncio.wait_for(service.receive(msg), timeout=5)
        assert "DURABLE_REPLY_ACCEPTED" in accepted.decision.reason_codes
        assert not accepted.outbound and provider.calls == 0
        assert core.cognition.store.get_message_by_dedupe(
            f"cognitive-input:{turn}").raw_text == msg.text
        repeated = await service.receive(msg)
        assert repeated.duplicate and not repeated.outbound
        assert provider.calls == 0
        assert await runtime.run_once()
        assert (await runtime.call("state", turn))["status"] == "retry_wait"
        assert core.messaging_store.get_outbound(service._outbound_id(msg)) is None
        assert not core.cognition.store.list_messages(turn_id=turn, speaker_id="ENTITY_AMORA")
        assert not core.cognition.store.list_segments(turn)
        provider.delay = 0
        # Keep another message while the earlier provider is unavailable.
        second = _dm(core, "durable-msg-2", text="E quais são suas partes?")
        assert "DURABLE_REPLY_ACCEPTED" in (await service.receive(second)).decision.reason_codes
        assert not await runtime.run_once()
        clock.advance(timedelta(seconds=6))
        service.provider_total_budget_seconds = 10
        service.provider_attempt_budget_seconds = 5
        assert await runtime.run_once()
        assert (await runtime.call("state", turn))["status"] == "ready"
        assert len(core.cognition.store.list_messages(turn_id=turn, speaker_id="ENTITY_AMORA")) >= 1
        assert len(core.cognition.store.list_segments(turn)) >= 1
        claim = core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD,
            account_scope="primary")
        assert len(claim) == 1 and claim[0].id == service._outbound_id(msg)
        assert claim[0].text == "Resposta remota estável."
        assert not claim[0].metadata["fallback_used"]
        assert core.messaging_store.ack_outbound(claim[0].id, sent=True,
            external_message_id="synthetic-discord-ack")
        assert await runtime.run_once()
        assert (await runtime.call("state", turn))["status"] == "sent"
        assert (await runtime.call("state", service._turn_id(second)))["status"] == "ready"
        assert (await service.receive(msg)).duplicate
        assert core.messaging_store.get_outbound(service._outbound_id(msg)).status.value == "sent"
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_integrated_checkpoint_survives_finalize_error_and_reuses_provider_result(lab, monkeypatch):
    core, clock, _ = lab
    runtime = await _start_runtime(core)
    provider = CountingStructuredProvider(name="durable-checkpoint")
    core.cognition.register_provider(provider)
    msg = _dm(core)
    turn = core.messaging._turn_id(msg)
    original = MessagingStore.persist_prepared_reply
    def fail_finalize(store, *args, **kwargs):
        original(store, *args, **kwargs)
        # Fail *after* history, outbound, and audit have been written, before
        # the job is ready. The single transaction must roll back all of them.
        raise RuntimeError("SYNTHETIC_STORAGE_FAILURE")
    try:
        assert "DURABLE_REPLY_ACCEPTED" in (await core.messaging.receive(msg)).decision.reason_codes
        monkeypatch.setattr(MessagingStore, "persist_prepared_reply", fail_finalize)
        assert await runtime.run_once()
        assert (await runtime.call("state", turn))["status"] == "retry_wait"
        assert not core.cognition.store.list_messages(turn_id=turn, speaker_id="ENTITY_AMORA")
        assert not core.cognition.store.list_segments(turn)
        assert core.messaging_store.get_outbound(core.messaging._outbound_id(msg)) is None
        audit = core.messaging_store.get_audit(platform=msg.platform,
            account_scope=msg.account_scope, conversation_id=msg.conversation_id,
            message_id=msg.message_id)
        assert not audit.responded
        assert provider.calls == 1
        monkeypatch.setattr(MessagingStore, "persist_prepared_reply", original)
        # Replace dispatcher instance, retain only database state.
        await runtime.stop()
        core.messaging.durable_replies = None
        resumed = core.messaging.configure_durable_replies()
        await resumed.start(background=False)
        clock.advance(timedelta(seconds=6))
        assert await resumed.run_once()
        assert provider.calls == 1
        assert (await resumed.call("state", turn))["status"] == "ready"
        assert len(core.cognition.store.list_messages(turn_id=turn, speaker_id="ENTITY_AMORA")) == 1
        assert len(core.cognition.store.list_segments(turn)) == 1
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_integrated_invalidation_before_generation_and_before_delivery(lab):
    core, clock, _ = lab
    runtime = await _start_runtime(core)
    provider = CountingStructuredProvider(name="durable-invalidation")
    core.cognition.register_provider(provider)
    original = _dm(core)
    turn = core.messaging._turn_id(original)
    try:
        await core.messaging.receive(original)
        core.messaging.invalidate_transport(TransportInvalidation(
            platform=original.platform, account_scope=original.account_scope,
            conversation_id=original.conversation_id,
            native_message_id=original.native_source_message_id,
            reason="MESSAGE_EDITED", transport_revision=2))
        assert not await runtime.run_once()
        assert provider.calls == 0
        assert (await runtime.call("state", turn))["status"] == "cancelled"
        newer = _dm(core, "durable-msg-edit", revision=2,
            native=original.native_source_message_id, text="Agora fala sobre folhas.")
        assert "DURABLE_REPLY_ACCEPTED" in (await core.messaging.receive(newer)).decision.reason_codes
        assert await runtime.run_once()
        assert provider.calls == 1
        core.messaging.invalidate_transport(TransportInvalidation(
            platform=newer.platform, account_scope=newer.account_scope,
            conversation_id=newer.conversation_id,
            native_message_id=newer.native_source_message_id,
            reason="MESSAGE_DELETED"))
        assert core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD,
            account_scope="primary") == ()
        assert not await runtime.run_once()
        assert (await runtime.call("state", core.messaging._turn_id(newer)))["status"] == "cancelled"
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_integrated_unknown_sender_and_local_only_never_join_queue(lab):
    core, clock, _ = lab
    runtime = await _start_runtime(core)
    provider = CountingStructuredProvider(name="durable-auth")
    core.cognition.register_provider(provider)
    try:
        denied = _dm(core, actor="999999999999999999")
        result = await core.messaging.receive(denied)
        assert result.decision.action == RoutingAction.QUARANTINE
        assert await runtime.call("state", core.messaging._turn_id(denied)) is None
        local = _dm(core, "local-msg").model_copy(update={"remote_processing_allowed": False})
        result = await core.messaging.receive(local)
        assert result.outbound
        assert await runtime.call("state", core.messaging._turn_id(local)) is None
        assert not await runtime.run_once()
        assert provider.calls == 0
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_integrated_atomic_admission_rolls_back_and_worker_can_retry(lab, monkeypatch):
    core, clock, _ = lab
    runtime = await _start_runtime(core)
    provider = CountingStructuredProvider(name="durable-admission")
    core.cognition.register_provider(provider)
    msg = _dm(core)
    turn = core.messaging._turn_id(msg)
    original = ReplyJobRepository._sql

    def fail_enqueue(repo, operation):
        if operation == "ENQUEUE_TURN":
            raise RuntimeError("SYNTHETIC_ADMISSION_FAILURE")
        return original(repo, operation)

    try:
        monkeypatch.setattr(ReplyJobRepository, "_sql", fail_enqueue)
        with pytest.raises(InboundGenerationBusy):
            await core.messaging.receive(msg)
        assert core.cognition.store.get_message_by_dedupe(f"cognitive-input:{turn}") is None
        assert await runtime.call("state", turn) is None
        assert provider.calls == 0
        monkeypatch.setattr(ReplyJobRepository, "_sql", original)
        accepted = await core.messaging.receive(msg)
        assert "DURABLE_REPLY_ACCEPTED" in accepted.decision.reason_codes
        assert core.cognition.store.get_message_by_dedupe(f"cognitive-input:{turn}").raw_text == msg.text
        assert (await runtime.call("state", turn))["status"] == "pending"
        assert await runtime.run_once()
        assert provider.calls == 1
        assert (await runtime.call("state", turn))["status"] == "ready"
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_integrated_restart_in_drain_mode_preserves_accepted_work(lab):
    core, clock, _ = lab
    runtime = await _start_runtime(core)
    provider = CountingStructuredProvider(name="durable-drain")
    core.cognition.register_provider(provider)
    msg = _dm(core)
    turn = core.messaging._turn_id(msg)
    try:
        await core.messaging.receive(msg)
        assert (await runtime.status())["unsettled"] == 1
        await runtime.stop()
        core.messaging.durable_replies = None
        recovered = core.messaging.configure_durable_replies()
        recovered.stop_admissions()
        await recovered.start(background=False)
        assert (await recovered.status())["mode"] == "drain"
        assert (await core.messaging.receive(msg)).duplicate
        new = _dm(core, "new-during-drain")
        with pytest.raises(InboundGenerationBusy):
            await core.messaging.receive(new)
        assert await recovered.call("state", core.messaging._turn_id(new)) is None
        assert await recovered.run_once()
        assert (await recovered.call("state", turn))["status"] == "ready"
        claim = core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD,
            account_scope="primary")
        assert len(claim) == 1
        assert core.messaging_store.ack_outbound(claim[0].id, sent=True,
            external_message_id="synthetic-ack-drain")
        assert not await recovered.run_once()
        assert (await recovered.status())["unsettled"] == 0
        assert provider.calls == 1
    finally:
        await _stop_runtime(core)


def test_rollout_requires_explicit_approval(monkeypatch):
    from amora.storage.reply_job_rollout import durable_reply_mode, APPROVAL
    monkeypatch.delenv("AMORA_DURABLE_REPLY_MODE", raising=False)
    monkeypatch.delenv("AMORA_DURABLE_REPLY_APPROVAL", raising=False)
    assert durable_reply_mode() == "off"
    monkeypatch.setenv("AMORA_DURABLE_REPLY_MODE", "accept")
    with pytest.raises(ValueError, match="DURABLE_REPLY_EXPLICIT_OPT_IN_REQUIRED"):
        durable_reply_mode()
    monkeypatch.setenv("AMORA_DURABLE_REPLY_APPROVAL", APPROVAL)
    assert durable_reply_mode() == "accept"
    monkeypatch.setenv("AMORA_DURABLE_REPLY_MODE", "drain")
    assert durable_reply_mode() == "drain"
    monkeypatch.setenv("AMORA_DURABLE_REPLY_MODE", "off")
    with pytest.raises(ValueError, match="DURABLE_REPLY_EXPLICIT_OPT_IN_REQUIRED"):
        durable_reply_mode()


def test_candidate_schema_verified_and_refuses_missing_index(lab):
    core, clock, queue = lab
    assert queue.verify_schema()["version"] == 1
    if core.database.backend.capabilities.name == "sqlite":
        with core.database._lock, core.database._conn:
            core.database._conn.execute("DROP INDEX idx_messaging_reply_due")
    else:
        with closing(_raw()) as raw:
            raw.execute("DROP INDEX amora_pg_v18_lab.idx_messaging_reply_due")
    with pytest.raises(ValueError, match="REPLY_.*SCHEMA.*"):
        queue.verify_schema()



def test_pg_candidate_schema_refuses_weakened_check_with_same_count(lab):
    core, _, queue = lab
    if core.database.backend.capabilities.name != "postgres-candidate":
        pytest.skip("PG18 catalog regression")
    assert queue.verify_schema()["version"] == 1
    from psycopg import sql
    with closing(_raw()) as raw:
        constraint = raw.execute("""SELECT conname FROM pg_constraint
            WHERE conrelid='amora_pg_v18_lab.messaging_reply_jobs'::regclass
            AND contype='c' AND pg_get_constraintdef(oid) LIKE '%transport_revision%'"""
        ).fetchone()[0]
        raw.execute(sql.SQL("ALTER TABLE amora_pg_v18_lab.messaging_reply_jobs "
                            "DROP CONSTRAINT {}, ADD CONSTRAINT {} CHECK (transport_revision >= 0)")
                    .format(sql.Identifier(constraint), sql.Identifier(constraint)))
    with pytest.raises(ValueError, match="REPLY_SCHEMA_STRUCTURAL_MISMATCH"):
        queue.verify_schema()


@pytest.mark.asyncio
async def test_integrated_native_backup_restore_new_core_preserves_ready_and_pending(lab, tmp_path, monkeypatch):
    """Synthetic snapshot + new Core instance, never a production destination."""
    core, clock, _ = lab
    pg = core.database.backend.capabilities.name == "postgres-candidate"
    if pg and os.getenv("GITHUB_ACTIONS") != "true":
        pytest.skip("Native PG18 dump/restore uses the disposable GitHub service")
    runtime = await _start_runtime(core)
    provider = CountingStructuredProvider(name="durable-before-restore")
    core.cognition.register_provider(provider)
    first = _dm(core, "ready-before-backup")
    second = _dm(core, "pending-before-backup", text="E como as árvores crescem?")
    first_turn, second_turn = core.messaging._turn_id(first), core.messaging._turn_id(second)
    first_outbound = core.messaging._outbound_id(first)
    backend = core.database.backend
    native_dump = "/tmp/amora-run01-replies-synthetic.dump"
    restored = None
    service = None
    try:
        await core.messaging.receive(first)
        assert await runtime.run_once()
        await core.messaging.receive(second)
        assert (await runtime.call("state", first_turn))["status"] == "ready"
        assert (await runtime.call("state", second_turn))["status"] == "pending"
        assert provider.calls == 1
        await _stop_runtime(core)
        if not pg:
            snapshot = core.database.backup_to(tmp_path / "reply-snapshot.db")
        core.close()
        if pg:
            from test_postgres_run2_native_backup_restore import _docker, _service_container
            service = _service_container()
            _docker("exec", service, "pg_dump", "-U", "amora_ci",
                "-d", "amora_pg_synthetic", "-n", "amora_pg_v18_lab",
                "--format=custom", "--no-owner", "--no-acl", "--file", native_dump)
            assert int(_docker("exec", service, "stat", "-c", "%s", native_dump)) > 1000
            with closing(_raw()) as raw:
                raw.execute("DROP SCHEMA amora_pg_v18_lab CASCADE")
            _docker("exec", service, "pg_restore", "-U", "amora_ci",
                "-d", "amora_pg_synthetic", "--no-owner", "--no-acl",
                "--exit-on-error", native_dump)
        monkeypatch.setenv("AMORA_HOME", str(tmp_path / "restored-home"))
        if not pg:
            AmoraDatabase.restore_backup(snapshot, tmp_path / "restored-home" / "data" / "amora.db")
        restored = AmoraCore.bootstrap(clock=clock, storage=backend)
        recovered = restored.messaging.configure_durable_replies()
        recovered.stop_admissions()
        await recovered.start(background=False)
        next_provider = CountingStructuredProvider(name="durable-after-restore")
        restored.cognition.register_provider(next_provider)
        assert (await recovered.status())["unsettled"] == 2
        assert (await recovered.call("state", first_turn))["status"] == "ready"
        assert (await recovered.call("state", second_turn))["status"] == "pending"
        assert (await restored.messaging.receive(first)).duplicate
        assert not await recovered.run_once()
        assert next_provider.calls == 0
        claims = restored.messaging_store.claim_outbound(
            platform=MessagingPlatform.DISCORD, account_scope="primary")
        assert len(claims) == 1 and claims[0].id == first_outbound
        assert restored.messaging_store.ack_outbound(first_outbound, sent=True,
            external_message_id="synthetic-restored-ack")
        assert await recovered.run_once()
        assert next_provider.calls == 1
        assert (await recovered.call("state", first_turn))["status"] == "sent"
        assert (await recovered.call("state", second_turn))["status"] == "ready"
        assert restored.cognition.store.get_message_by_dedupe(
            f"cognitive-input:{second_turn}").raw_text == second.text
        assert len(restored.cognition.store.list_messages(
            turn_id=first_turn, speaker_id="ENTITY_AMORA")) == 1
    finally:
        if restored is not None:
            await _stop_runtime(restored)
            restored.close()
        if service is not None:
            _docker("exec", service, "rm", "-f", native_dump)
        await _stop_runtime(core)
        core.close()


@pytest.mark.asyncio
async def test_run02_rate_limit_persists_retry_after_and_recovers(lab):
    from amora.cognition.providers import ProviderError
    core, clock, _ = lab
    runtime = await _start_runtime(core)
    provider = CountingStructuredProvider(name="durable-rate-limit")
    original = provider.realize
    attempts = []
    async def realize(context):
        attempts.append(context.turn_id)
        if len(attempts) == 1:
            raise ProviderError("synthetic limit", status_code=429, retry_after="120")
        return await original(context)
    provider.realize = realize
    core.cognition.register_provider(provider)
    msg = _dm(core)
    turn = core.messaging._turn_id(msg)
    try:
        await core.messaging.receive(msg)
        assert await runtime.run_once()
        state = await runtime.call("state", turn)
        assert state["status"] == "retry_wait"
        assert len(attempts) == 1
        assert not core.cognition.store.list_messages(turn_id=turn, speaker_id="ENTITY_AMORA")
        clock.advance(timedelta(seconds=119))
        assert not await runtime.run_once()
        assert len(attempts) == 1
        clock.advance(timedelta(seconds=2))
        assert await runtime.run_once()
        assert len(attempts) == 2
        assert (await runtime.call("state", turn))["status"] == "ready"
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_run02_permanent_provider_auth_failure_blocks_without_social_fallback(lab):
    from amora.cognition.providers import ProviderError
    core, clock, _ = lab
    runtime = await _start_runtime(core)
    provider = CountingStructuredProvider(name="durable-invalid-key")
    attempts = []
    async def realize(context):
        attempts.append(context.turn_id)
        raise ProviderError("synthetic auth failure", status_code=401)
    provider.realize = realize
    core.cognition.register_provider(provider)
    msg = _dm(core)
    turn = core.messaging._turn_id(msg)
    try:
        await core.messaging.receive(msg)
        assert await runtime.run_once()
        assert (await runtime.call("state", turn))["status"] == "blocked"
        clock.advance(timedelta(seconds=600))
        assert not await runtime.run_once()
        assert len(attempts) == 1
        assert not core.cognition.store.list_messages(turn_id=turn, speaker_id="ENTITY_AMORA")
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
@pytest.mark.parametrize("normal_fits", [True, False])
async def test_run03_durable_adaptive_only_after_full_context_rejection(lab, normal_fits):
    from amora.cognition.context_budget import ContextBudgetExceeded
    core, clock, _ = lab
    runtime = await _start_runtime(core)
    provider = CountingStructuredProvider(name="durable-adaptive-synthetic")
    original_realize = provider.realize
    calls = []
    text = ("Amora, explica estas ideias. " + "x" * 4400
            + " Mas não vou escolher A, escolhi B. Qual a diferença?")
    async def normal(context):
        calls.append("full")
        assert context.user_text == text
        if not normal_fits:
            raise ContextBudgetExceeded(8000, 5200)
        return await original_realize(context)
    async def adaptive(context):
        calls.append("adaptive")
        assert context.user_text == text
        return await original_realize(context)
    provider.realize = normal
    provider.realize_adaptive = adaptive
    core.cognition.register_provider(provider)
    msg = _dm(core, text=text)
    turn = core.messaging._turn_id(msg)
    try:
        accepted = await core.messaging.receive(msg)
        assert "DURABLE_REPLY_ACCEPTED" in accepted.decision.reason_codes
        assert await runtime.run_once()
        state = await runtime.call("state", turn)
        assert state["status"] == "ready"
        assert calls == (["full"] if normal_fits else ["full", "adaptive"])
        assert core.cognition.store.get_message_by_dedupe(
            f"cognitive-input:{turn}").raw_text == text
        assert len(core.cognition.store.list_messages(
            turn_id=turn, speaker_id="ENTITY_AMORA")) == 1
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_run06_causal_snapshot_preserves_followup_jobs_and_original_text(lab):
    core, clock, _ = lab
    runtime = await _start_runtime(core)
    provider = CountingStructuredProvider(name="durable-causal-synthetic")
    original_realize = provider.realize
    snapshots = []
    async def capture(context):
        snapshots.append(context)
        return await original_realize(context)
    provider.realize = capture
    core.cognition.register_provider(provider)
    first = _dm(core, "causal-first", text="Quero saber sobre Godot para PC.")
    try:
        assert "DURABLE_REPLY_ACCEPTED" in (await core.messaging.receive(first)).decision.reason_codes
        clock.advance(timedelta(seconds=1))
        second = _dm(core, "causal-second", text="Na verdade, Godot para Android.")
        assert "DURABLE_REPLY_ACCEPTED" in (await core.messaging.receive(second)).decision.reason_codes
        clock.advance(timedelta(seconds=1))
        third = _dm(core, "causal-third", text="Mudando de assunto, como funciona a chuva?")
        assert "DURABLE_REPLY_ACCEPTED" in (await core.messaging.receive(third)).decision.reason_codes
        assert await runtime.run_once()
        assert len(snapshots) == 1
        ctx = snapshots[0]
        assert ctx.user_text == first.text
        assert [x["turn_id"] for x in ctx.causal_followups] == [str(core.messaging._turn_id(second))]
        assert ctx.causal_followups[0]["literal_text"] == second.text
        assert all(x["turn_id"] not in {str(core.messaging._turn_id(second)),str(core.messaging._turn_id(third))}
                   for x in ctx.recent_messages)
        assert (await runtime.call("state",core.messaging._turn_id(first)))["status"] == "ready"
        for message in (second,third):
            turn = core.messaging._turn_id(message)
            assert (await runtime.call("state",turn))["status"] == "pending"
            assert core.cognition.store.get_message_by_dedupe(f"cognitive-input:{turn}").raw_text == message.text
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_run07_burst_restart_duplicate_ack_and_queue_accounting(lab):
    """Twelve unique accepted inputs, repeated ingress, restart, exact ACKs."""
    core, clock, _ = lab
    runtime = await _start_runtime(core)
    provider = CountingStructuredProvider(name="durable-run07-synthetic")
    core.cognition.register_provider(provider)
    service = core.messaging
    inputs = [_dm(core, f"run07-{i}", text=f"Explique o conceito número {i}.")
              for i in range(12)]
    try:
        for message in inputs:
            message.timestamp = clock.now()
            accepted = await service.receive(message)
            assert "DURABLE_REPLY_ACCEPTED" in accepted.decision.reason_codes
            assert (await service.receive(message)).duplicate
            clock.advance(timedelta(seconds=1))
        status = await runtime.status()
        assert status["counts"].get("pending") == 12
        assert status["diagnostics"]["receipt_counts"] == {"new_ack":12,"duplicate_ack":12}
        assert await runtime.run_once()
        first = service._turn_id(inputs[0])
        assert (await runtime.call("state",first))["status"] == "ready"
        assert not await runtime.run_once()  # physical delivery fence
        await runtime.stop()
        service.durable_replies = None
        runtime = service.configure_durable_replies()
        await runtime.start(background=False)
        for index, message in enumerate(inputs):
            if index:
                assert await runtime.run_once()
            claim = core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD,
                account_scope="primary")
            assert len(claim) == 1 and claim[0].id == service._outbound_id(message)
            assert core.messaging_store.ack_outbound(claim[0].id, sent=True,
                external_message_id=f"run07-synthetic-ack-{index}")
            assert (await service.receive(message)).duplicate
        assert not await runtime.run_once()
        final = await runtime.status()
        assert final["counts"].get("sent") == 12
        assert final["unsettled"] == 0
        assert provider.calls == 12
        for message in inputs:
            turn = service._turn_id(message)
            assert core.cognition.store.get_message_by_dedupe(f"cognitive-input:{turn}").raw_text == message.text
            assert len(core.cognition.store.list_messages(turn_id=turn,speaker_id="ENTITY_AMORA")) == 1
        assert final["diagnostics"]["attempt_outcomes"] == {"ready":11}
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_run07_compiler_overflow_reaches_only_adaptive_route(lab, monkeypatch):
    from amora.cognition import context_budget
    core, clock, _ = lab
    runtime = await _start_runtime(core)
    provider = CountingStructuredProvider(name="durable-compiler-overflow")
    original = provider.realize
    text = "Explique estas ideias. " + "x" * 4400 + " Mas não escolhi A, escolhi B."
    estimate = context_budget.estimate_context
    monkeypatch.setattr(context_budget, "estimate_context",
        lambda context: context.token_budget + 1 if context.user_text == text else estimate(context))
    calls = []
    async def full(context):
        raise AssertionError("oversized canonical snapshot must not reach ordinary realization")
    async def adaptive(context):
        calls.append("adaptive")
        assert context._compiler_overflow
        assert "AMD_COMPILER_OVERFLOW" in context.reason_codes
        assert context.user_text == text
        return await original(context)
    provider.realize = full
    provider.realize_adaptive = adaptive
    core.cognition.register_provider(provider)
    msg = _dm(core, text=text)
    turn = core.messaging._turn_id(msg)
    try:
        assert "DURABLE_REPLY_ACCEPTED" in (await core.messaging.receive(msg)).decision.reason_codes
        assert await runtime.run_once()
        assert calls == ["adaptive"]
        assert (await runtime.call("state",turn))["status"] == "ready"
        assert core.cognition.store.get_message_by_dedupe(f"cognitive-input:{turn}").raw_text == text
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_visual_checkpoint_exact_turn_ttl_source_and_backend_reopen(lab):
    from amora.messaging.visual_checkpoint import VisualCheckpointStore, checkpoint_key
    from amora.messaging.models import AttachmentRef
    from test_discord_incoming_vision_ch2 import payload, observation

    core, clock, _ = lab
    _bind_bk(core)
    service = core.messaging
    image = payload()
    message = _dm(core, "visual-checkpoint", text="Descreva a cor.")
    message = InboundMessage.model_validate({
        **message.model_dump(), "attachments":[AttachmentRef(
            attachment_id=image.attachment_id, media_type="image/png").model_dump()],
        "incoming_image":image.model_dump(),
    })
    decision = AttentionDecision(action=RoutingAction.RESPOND, score=1,
        speaker_entity_id="PERSON_BK", speaker_actor_ref="synthetic",
        addressee_id="ENTITY_AMORA")
    calls = []
    class Vision:
        available = True
        async def analyze(self, image, user_text):
            calls.append(user_text)
            return observation()
    service.incoming_vision = Vision()
    first = await service._incoming_visual_observation(message, decision)
    assert first == observation()
    assert calls == [message.text]
    session = service._session_ref(message)
    native_hash = service._hash(message.native_source_message_id)
    args = (session, native_hash, 1,
        checkpoint_key(session, service._turn_id(message), native_hash, 1,image,message.text))
    # A separately opened backend connection proves that no RAM cache is needed.
    with closing(AmoraDatabase(core.database.path,backend=core.database.backend)) as reopened:
        service.visual_checkpoints = VisualCheckpointStore(reopened, clock)
        assert service.visual_checkpoints.load(*args) == first
        assert await service._incoming_visual_observation(message,decision) == first
        assert len(calls) == 1
        assert service.visual_checkpoints.load("another-session", *args[1:]) is None
        changed = message.model_copy(update={"text":"Transcreva o texto."})
        assert await service._incoming_visual_observation(changed,decision) == first
        assert len(calls) == 2
        no_consent = message.model_copy(update={"remote_processing_allowed":False})
        assert await service._incoming_visual_observation(no_consent,decision) is None
        assert len(calls) == 2
        clock.advance(timedelta(seconds=901))
        assert service.visual_checkpoints.load(*args) is None
        assert await service._incoming_visual_observation(message,decision) == first
        assert len(calls) == 3
        core.messaging.reflections.invalidate_source(session,native_hash,through_revision=1)
        assert service.visual_checkpoints.load(*args) is None
        assert await service._incoming_visual_observation(message,decision) is None
        assert len(calls) == 3
    service.visual_checkpoints = VisualCheckpointStore(core.database,clock)


@pytest.mark.asyncio
async def test_visual_reference_across_turns_reopened_backend_and_exact_reply(lab):
    from amora.messaging.visual_checkpoint import VisualCheckpointStore
    from amora.messaging.models import AttachmentRef
    from amora.cognition.visual_context import visual_context_for_request
    from test_discord_incoming_vision_ch2 import payload, observation
    core, clock, _ = lab
    _bind_bk(core)
    service = core.messaging
    image = payload()
    decision = AttentionDecision(action=RoutingAction.RESPOND, score=1,
        speaker_entity_id="PERSON_BK", speaker_actor_ref="synthetic",
        addressee_id="ENTITY_AMORA")
    calls = []
    class Vision:
        available = True
        async def analyze(self,image,user_text):
            calls.append(user_text)
            return {**observation(),"description":"Imagem observada "+str(len(calls))}
    service.incoming_vision = Vision()
    def photo(mid,text):
        msg = _dm(core,mid,text=text)
        return InboundMessage.model_validate({**msg.model_dump(),
            "attachments":[AttachmentRef(attachment_id=image.attachment_id,media_type="image/png").model_dump()],
            "incoming_image":image.model_dump()})
    first = photo("visual-history-first","Descreva a cor.")
    prior = await service._incoming_visual_observation(first,decision)
    clock.advance(timedelta(seconds=2))
    with closing(AmoraDatabase(core.database.path,backend=core.database.backend)) as reopened:
        service.visual_checkpoints = VisualCheckpointStore(reopened,clock)
        follow = _dm(core,"visual-history-follow",text="Qual a cor da imagem anterior?")
        recovered = await service._incoming_visual_observation(follow,decision)
        assert recovered["historical"] == prior
        assert recovered["status"] == "analyzed" and recovered["observation_version"] == 4
        assert len(calls) == 1
        assert visual_context_for_request(recovered,remote=True,private=True,mode="reply")["historical"] == prior
        assert visual_context_for_request(recovered,remote=True,private=False,mode="reply") is None
        assert visual_context_for_request(recovered,remote=True,private=True,mode="reflection") is None
        assert await service._incoming_visual_observation(follow.model_copy(update={
            "remote_processing_allowed":False}),decision) is None
        other = follow.model_copy(update={"conversation_id":"other-dm"})
        assert (await service._incoming_visual_observation(other,decision))["status"] == "unavailable"
        clock.advance(timedelta(seconds=2))
        second = photo("visual-history-second","Compare esta com a imagem anterior.")
        comparison = await service._incoming_visual_observation(second,decision)
        assert comparison["historical"] == prior
        assert comparison["current"]["description"] == "Imagem observada 2"
        assert len(calls) == 2
        clock.advance(timedelta(seconds=2))
        reply = _dm(core,"visual-history-reply",text="E a cor dessa foto?").model_copy(
            update={"reply_to_message_id":first.native_source_message_id})
        exact = await service._incoming_visual_observation(reply,decision)
        assert exact["reference_kind"] == "reply" and exact["historical"] == prior
        session = service._session_ref(second)
        core.messaging.reflections.invalidate_source(session,
            service._hash(second.native_source_message_id),through_revision=1)
        latest = _dm(core,"visual-history-after-delete",text="Descreva a última foto.")
        missing = await service._incoming_visual_observation(latest,decision)
        assert missing["status"] == "unavailable" and "historical" not in missing
        # An exact older reply is independent of the invalidated newer source.
        assert (await service._incoming_visual_observation(reply,decision))["historical"] == prior
        clock.advance(timedelta(seconds=86401))
        expired = reply.model_copy(update={"timestamp":clock.now()})
        assert (await service._incoming_visual_observation(expired,decision))["status"] == "unavailable"
        assert len(calls) == 2
    service.visual_checkpoints = VisualCheckpointStore(core.database,clock)


@pytest.mark.asyncio
@pytest.mark.parametrize("related", [True, False])
async def test_inflight_correction_replans_only_related_uncommitted_reply(lab, related):
    core, clock, _ = lab
    runtime = await _start_runtime(core)
    service = core.messaging
    provider = CountingStructuredProvider(name="durable-causal-replan")
    began, release = asyncio.Event(), asyncio.Event()
    calls = []
    async def realize(context):
        calls.append((context.user_text, list(context.causal_followups)))
        if len(calls) == 1:
            began.set()
            await release.wait()
        text = ("Resposta revisada para Android." if context.causal_followups
                else "Resposta inicial sobre Godot.")
        return {"moves":[{"kind":context.dialogue_plan["response_moves"][0],"text":text}],
                "closing":"statement","_usage":{"input_tokens":12,"output_tokens":4,"status_code":200}}
    provider.realize = realize
    core.cognition.register_provider(provider)
    first = _dm(core,"causal-replan-first",text="Explique Godot para PC.")
    first_turn = service._turn_id(first)
    work = None
    try:
        assert "DURABLE_REPLY_ACCEPTED" in (await service.receive(first)).decision.reason_codes
        work = asyncio.create_task(runtime.run_once())
        await asyncio.wait_for(began.wait(),timeout=10)
        clock.advance(timedelta(seconds=1))
        next_text = "Na verdade, Godot para Android." if related else "Outra pergunta, qual é a capital?"
        second = _dm(core,"causal-replan-second",text=next_text)
        second_turn = service._turn_id(second)
        assert "DURABLE_REPLY_ACCEPTED" in (await service.receive(second)).decision.reason_codes
        assert core.messaging_store.get_outbound(service._outbound_id(first)) is None
        assert not core.cognition.store.list_segments(first_turn)
        release.set()
        assert await asyncio.wait_for(work,timeout=20)
        assert (await runtime.call("state",first_turn))["status"] == "ready"
        assert (await runtime.call("state",second_turn))["status"] == "pending"
        assert len(calls) == (2 if related else 1)
        assert calls[0] == (first.text,[])
        if related:
            assert calls[1][0] == first.text
            assert calls[1][1][0]["literal_text"] == next_text
            assert calls[1][1][0]["turn_id"] == str(second_turn)
        outbound = core.messaging_store.get_outbound(service._outbound_id(first))
        assert outbound.text == ("Resposta revisada para Android." if related else "Resposta inicial sobre Godot.")
        history = core.cognition.store.list_messages(turn_id=first_turn,speaker_id="ENTITY_AMORA")
        assert history
        if related:
            assert all("Resposta inicial" not in (item.raw_text or "") for item in history)
            assert "DURABLE_CAUSAL_REPLANNED" in outbound.metadata["cognitive_reason_codes"]
        assert core.cognition.store.get_message_by_dedupe("cognitive-input:"+str(second_turn)).raw_text == next_text
        assert not core.cognition.store.list_segments(second_turn)
    finally:
        release.set()
        if work is not None and not work.done():
            work.cancel()
            await asyncio.gather(work,return_exceptions=True)
        await _stop_runtime(core)


@pytest.mark.asyncio
@pytest.mark.parametrize("uncertain",[False,True])
async def test_durable_discord_segment_receipts_reclaim_and_uncertain_outcome(lab,uncertain,monkeypatch):
    from amora.messaging.delivery_segments import DeliverySegmentRequest, split_delivery_text, CHECKPOINT
    from amora.cognition import CognitiveResponsePlan
    core, clock, _ = lab
    runtime=await _start_runtime(core)
    service=core.messaging
    long_text=("Texto sintético de recuperação. "*180).strip()
    async def generate(claim,message,request):
        return CognitiveResponsePlan(turn_id=request.turn_id,display_text=long_text,
            speech_text=long_text,provider="synthetic",model="synthetic",
            metadata={"realization_validated":True})
    monkeypatch.setattr(runtime,"_generate",generate)
    msg=_dm(core,"durable-long-delivery")
    turn=service._turn_id(msg)
    def claim(store,enabled=True):
        return store.claim_outbound(platform=MessagingPlatform.DISCORD,
            account_scope="primary",limit=1,lease_seconds=5,segmented_delivery=enabled)
    def ack(store,item,index,phase,sent=False,external=None):
        lease=item.metadata["discord_delivery_claim"]["lease"]
        return store.ack_delivery_segment(item.id,DeliverySegmentRequest(
            index=index,phase=phase,lease=lease),sent=sent,external_message_id=external)
    try:
        assert "DURABLE_REPLY_ACCEPTED" in (await service.receive(msg)).decision.reason_codes
        assert await runtime.run_once()
        store=core.messaging_store
        first,=claim(store)
        chunks=first.metadata["discord_delivery_claim"]["chunks"]
        assert chunks==split_delivery_text(long_text) and len(chunks)>=3
        receipt="123456789012345678"
        assert not store.ack_outbound(first.id,sent=True,external_message_id=receipt)
        assert not ack(store,first,1,"prepare")
        assert not ack(store,first,0,"confirm",True,receipt)
        assert ack(store,first,0,"prepare")
        assert ack(store,first,0,"confirm",True,receipt)
        assert ack(store,first,0,"confirm",True,receipt)
        assert not ack(store,first,0,"confirm",True,"123456789012345679")
        assert store.get_outbound(first.id).status.value=="claimed"
        assert (await runtime.call("state",turn))["status"]=="ready"
        if uncertain:
            assert ack(store,first,1,"prepare")
        clock.advance(timedelta(seconds=121))
        assert not claim(store,False)
        with closing(AmoraDatabase(core.database.path,backend=core.database.backend)) as db:
            reopened=type(store)(db,clock)
            recovered=claim(reopened)
            if uncertain:
                assert not recovered
                failed=reopened.get_outbound(first.id)
                assert failed.status.value=="failed"
                assert failed.metadata[CHECKPOINT]["receipts"]==[receipt]
                assert not claim(reopened)
            else:
                second,=recovered
                assert second.metadata["discord_delivery_claim"]["receipts"]==[receipt]
                assert not ack(reopened,first,1,"prepare")
                for index in range(1,len(chunks)):
                    assert ack(reopened,second,index,"prepare")
                    assert ack(reopened,second,index,"confirm",True,str(123456789012345678+index))
                completed=reopened.get_outbound(first.id)
                assert completed.status.value=="sent"
                assert len(completed.metadata[CHECKPOINT]["receipts"])==len(chunks)
                assert "delivery_confirmed_at" in completed.metadata
                assert "discord_delivery_claim" not in completed.metadata
                assert not claim(reopened)
        await runtime.run_once()
        assert (await runtime.call("state",turn))["status"]==("blocked" if uncertain else "sent")
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_run07_combined_failure_recovery_accounting(lab,monkeypatch,capsys):
    """One continuous workload: admission, outage, checkpoint, restart, drain and delivery."""
    from collections import Counter
    from time import perf_counter
    import json
    from amora.cognition.providers import ProviderError

    core, clock, _ = lab
    runtime = await _start_runtime(core)
    service = core.messaging
    provider = CountingStructuredProvider(name="run07-combined-faults")
    original_realize = provider.realize
    attempts = Counter()
    times = []
    core.cognition.register_provider(provider)
    inputs = [_dm(core,f"combined-{i}",text=f"Explique o conceito sintético número {i}.")
              for i in range(24)]
    by_turn = {str(service._turn_id(msg)):i for i,msg in enumerate(inputs)}
    async def realize(context):
        index = by_turn[str(context.turn_id)]
        attempts[index] += 1
        if index in {0,8,16} and attempts[index]==1:
            raise ProviderError("synthetic temporary failure",status_code=503)
        return await original_realize(context)
    provider.realize=realize
    original_finalize=MessagingStore.persist_prepared_reply
    inject={"active":False}
    def finalize(store,*args,**kwargs):
        result=original_finalize(store,*args,**kwargs)
        if inject["active"]:
            inject["active"]=False
            raise RuntimeError("SYNTHETIC_FINALIZE_ROLLBACK")
        return result
    monkeypatch.setattr(MessagingStore,"persist_prepared_reply",finalize)
    cancelled={5,13,21}
    receipts=set()
    restarts=0
    async def restart():
        nonlocal runtime,restarts
        await runtime.stop()
        service.durable_replies=None
        runtime=service.configure_durable_replies()
        await runtime.start(background=False)
        restarts+=1
    started=perf_counter()
    try:
        for index,msg in enumerate(inputs):
            msg.timestamp=clock.now()
            assert "DURABLE_REPLY_ACCEPTED" in (await service.receive(msg)).decision.reason_codes
            assert (await service.receive(msg)).duplicate
            clock.advance(timedelta(seconds=1))
        for index in cancelled:
            msg=inputs[index]
            core.messaging.invalidate_transport(TransportInvalidation(
                platform=msg.platform,account_scope=msg.account_scope,
                conversation_id=msg.conversation_id,native_message_id=msg.native_source_message_id,
                reason="MESSAGE_DELETED"))
        for index,msg in enumerate(inputs):
            turn=service._turn_id(msg)
            if index in cancelled:
                assert (await runtime.call("state",turn))["status"]=="cancelled"
                assert not attempts[index]
                continue
            begin=perf_counter()
            if index in {3,11,19}:
                inject["active"]=True
            assert await runtime.run_once()
            state=await runtime.call("state",turn)
            if index in {0,8,16,3,11,19}:
                assert state["status"]=="retry_wait"
                assert core.messaging_store.get_outbound(service._outbound_id(msg)) is None
                assert not core.cognition.store.list_segments(turn)
                await restart()
                # Simulated provider cooldown and durable retry delay, not a wall-clock sleep.
                clock.advance(timedelta(seconds=600))
                assert await runtime.run_once()
                state=await runtime.call("state",turn)
            assert state["status"]=="ready"
            if index in {4,12,20}:
                await restart()
                assert not await runtime.run_once()
            claimed=core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD,
                account_scope="primary",limit=1)
            assert len(claimed)==1 and claimed[0].id==service._outbound_id(msg)
            receipt=f"combined-synthetic-ack-{index}"
            assert receipt not in receipts
            assert core.messaging_store.ack_outbound(claimed[0].id,sent=True,external_message_id=receipt)
            assert not core.messaging_store.ack_outbound(claimed[0].id,sent=True,external_message_id=receipt)
            receipts.add(receipt)
            # Reconcile without claiming the next input until its injected fault is configured.
            await runtime.call("reconcile_outbound")
            assert (await runtime.call("state",turn))["status"]=="sent"
            times.append((perf_counter()-begin)*1000)
        assert not await runtime.run_once()
        final=await runtime.status()
        assert final["counts"].get("sent")==21
        assert final["counts"].get("cancelled")==3
        assert final["unsettled"]==0
        assert len(receipts)==21 and restarts==9
        assert sum(attempts.values())==24
        for index,msg in enumerate(inputs):
            turn=service._turn_id(msg)
            assert core.cognition.store.get_message_by_dedupe("cognitive-input:"+str(turn)).raw_text==msg.text
            history=core.cognition.store.list_messages(turn_id=turn,speaker_id="ENTITY_AMORA")
            assert len(history)==(0 if index in cancelled else 1)
            assert attempts[index]==(0 if index in cancelled else 2 if index in {0,8,16} else 1)
        ordered=sorted(times)
        import math
        with capsys.disabled():
            print("RUN07_COMBINED_JSON="+json.dumps({
                "backend":core.database.backend.capabilities.name,
                "accepted":24,"sent_synthetic":21,"cancelled":3,
                "provider_attempts":sum(attempts.values()),"dispatcher_restarts":restarts,
                "provider_faults":3,"finalize_faults":3,
                "wall_seconds":round(perf_counter()-started,3),
                "processing_ms_p50":round(ordered[math.ceil(len(ordered)*.5)-1],3),
                "processing_ms_p95":round(ordered[math.ceil(len(ordered)*.95)-1],3),
                "clock":"simulated_retry_waits","physical_delivery":False
            },sort_keys=True),flush=True)
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_stabilization_section_checkpoint_resume_on_reopened_store(lab):
    from amora.cognition.models import CognitiveContext, JournalStage
    from amora.cognition.detail_sections import DetailSession, section_scopes
    from amora.cognition.providers import ProviderError
    core,clock,_=lab
    request=_request().model_copy(update={"text":"Crie uma documentação técnica detalhada da arquitetura e do armazenamento. Explique cada sistema com seus algoritmos e restrições."})
    ctx=CognitiveContext(turn_id=request.turn_id,created_at=clock.now(),speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",user_text=request.text,privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,dialogue_plan={"response_moves":["answer_question"],"depth":"deep"})
    scopes=section_scopes(request.text)
    assert len(scopes)==2
    authorized=True
    async def guard():
        return authorized
    primary=CountingStructuredProvider(name="section-primary")
    secondary=CountingStructuredProvider(name="section-secondary")
    core.cognition.register_provider(primary)
    core.cognition.register_provider(secondary)
    calls=[]
    async def first(c):
        calls.append(c._detail_section["index"])
        assert c.user_text==request.text
        if c._detail_section["index"]==2:
            raise ProviderError("temporary",status_code=503)
        return {"moves":[{"kind":"answer_question","text":"Primeira seção: arquitetura e armazenamento."}],
                "closing":"statement","_usage":{"input_tokens":20,"output_tokens":10,"status_code":200}}
    session=DetailSession(core.cognition,request,ctx,scopes,guard,False)
    with pytest.raises(ProviderError):
        await session.run(first,primary.descriptor)
    assert calls==[1,2] and "incompleta" in session.partial().text
    assert not core.cognition.store.list_segments(request.turn_id)
    store=core.cognition.store
    reopened=type(store)(store.database_path,clock,backend=store.backend)
    try:
        core.cognition.store=reopened
        recovered=DetailSession(core.cognition,request,ctx.model_copy(update={"created_at":clock.now()}),scopes,guard,False)
        async def second(c):
            calls.append(c._detail_section["index"])
            return {"moves":[{"kind":"answer_question","text":"Segunda seção: algoritmos e restrições."}],
                    "closing":"statement","_usage":{"input_tokens":21,"output_tokens":11,"status_code":200}}
        result=await recovered.run(second,secondary.descriptor)
        assert calls==[1,2,2]
        assert len(result["moves"])==2 and recovered.partial() is None
        assert recovered.providers==[(primary.descriptor.name,primary.descriptor.model),
            (secondary.descriptor.name,secondary.descriptor.model)]
        assert len([j for j in reopened.journal_for_turn(request.turn_id)
            if j.stage==JournalStage.DETAIL_SECTION_CHECKPOINT])==2
        assert not reopened.list_segments(request.turn_id)
        authorized=False
        revoked=DetailSession(core.cognition,request,ctx,scopes,guard,False)
        with pytest.raises(InboundGenerationLost):
            await revoked.run(second,secondary.descriptor)
        authorized=True
        changed=DetailSession(core.cognition,request.model_copy(update={"metadata":{**request.metadata,"transport_revision":2}}),ctx,scopes,guard,False)
        await changed.load()
        assert not changed.frames
        clock.advance(timedelta(seconds=901))
        expired=DetailSession(core.cognition,request,ctx,scopes,guard,False)
        await expired.load()
        assert not expired.frames
        assert calls==[1,2,2]
    finally:
        core.cognition.store=store
        reopened.close()


@pytest.mark.asyncio
async def test_stabilization_visual_dispute_is_scoped_and_does_not_fake_reinspection(lab):
    from amora.messaging.visual_checkpoint import VisualCheckpointStore
    from amora.messaging.models import AttachmentRef
    from amora.cognition.visual_context import visual_context_for_request
    from test_discord_incoming_vision_ch2 import payload,observation
    core,clock,_=lab
    _bind_bk(core)
    service=core.messaging
    image=payload()
    decision=AttentionDecision(action=RoutingAction.RESPOND,score=1,
        speaker_entity_id="PERSON_BK",speaker_actor_ref="synthetic",addressee_id="ENTITY_AMORA")
    calls=[]
    class Vision:
        available=True
        async def analyze(self,image,user_text):
            calls.append(user_text)
            return {**observation(),"description":"Leitura sintética "+str(len(calls))}
    service.incoming_vision=Vision()
    def photo(mid,text):
        msg=_dm(core,mid,text=text)
        return InboundMessage.model_validate({**msg.model_dump(),
            "attachments":[AttachmentRef(attachment_id=image.attachment_id,media_type="image/png").model_dump()],
            "incoming_image":image.model_dump()})
    first=photo("review-photo","Descreva a imagem.")
    await service._incoming_visual_observation(first,decision)
    clock.advance(timedelta(seconds=1))
    review=_dm(core,"review-only",text="Reanalise a imagem anterior.")
    r=await service._incoming_visual_observation(review,decision)
    assert r["review_requested"] is True and "current" not in r and len(calls)==1
    clock.advance(timedelta(seconds=1))
    dispute=_dm(core,"review-dispute",text="Você errou na análise da imagem anterior.")
    denied=dispute.model_copy(update={"remote_processing_allowed":False})
    assert await service._incoming_visual_observation(denied,decision) is None
    r=await service._incoming_visual_observation(dispute,decision)
    assert r["historical_disputed"] is True and r["historical"]["status"]=="unavailable"
    assert visual_context_for_request(r,remote=True,private=True,mode="reply")["historical_disputed"] is True
    assert len(calls)==1
    clock.advance(timedelta(seconds=1))
    with closing(AmoraDatabase(core.database.path,backend=core.database.backend)) as reopened:
        service.visual_checkpoints=VisualCheckpointStore(reopened,clock)
        follow=_dm(core,"review-recall",text="Descreva a imagem anterior.")
        r=await service._incoming_visual_observation(follow,decision)
        assert r["historical_disputed"] is True and r["historical"]["description"]==""
        assert len(calls)==1
        fresh=photo("review-new-photo","Compare com a imagem anterior. Olhe os olhos.")
        r=await service._incoming_visual_observation(fresh,decision)
        assert r["historical_disputed"] is True and r["current"]["description"]=="Leitura sintética 2"
        assert r["status"]=="partial" and len(calls)==2
    service.visual_checkpoints=VisualCheckpointStore(core.database,clock)


@pytest.mark.asyncio
async def test_run02_real_raster_review_compare_and_scope(lab):
    from amora.messaging.models import AttachmentRef
    from amora.messaging.visual_pixels import VisualPreviewCache
    from amora.cognition.visual_context import visual_context_for_request
    from test_run02_visual_pixels import photo
    from test_discord_incoming_vision_ch2 import observation
    core,clock,_=lab
    _bind_bk(core)
    service=core.messaging
    calls=[]
    class Vision:
        available=True
        async def analyze(self,image,user_text):
            calls.append((image.content_base64,user_text))
            return {**observation(),"description":"Fresh observation "+str(len(calls))}
    service.incoming_vision=Vision()
    decision=AttentionDecision(action=RoutingAction.RESPOND,score=1,
        speaker_entity_id="PERSON_BK",speaker_actor_ref="synthetic",addressee_id="ENTITY_AMORA")
    a=photo((0,0,0,255)).model_copy(update={"attachment_id":"987654321098765432"})
    b=photo((255,0,0,255)).model_copy(update={"attachment_id":"987654321098765433"})
    def inbound(mid,text,image):
        msg=_dm(core,mid,text=text)
        return InboundMessage.model_validate({**msg.model_dump(),
            "attachments":[AttachmentRef(attachment_id=image.attachment_id,media_type="image/png").model_dump()],
            "incoming_image":image.model_dump()})
    await service._incoming_visual_observation(inbound("pixel-old","Descreva a imagem.",a),decision)
    clock.advance(timedelta(seconds=1))
    review=_dm(core,"pixel-review",text="Reanalise a imagem anterior.")
    assert await service._incoming_visual_observation(review.model_copy(
        update={"remote_processing_allowed":False}),decision) is None
    assert len(calls)==1
    result=await service._incoming_visual_observation(review,decision)
    assert result["reinspection"]["description"]=="Fresh observation 2"
    assert calls[0][0]==calls[1][0]
    assert visual_context_for_request(result,remote=True,private=True,mode="reply")
    clock.advance(timedelta(seconds=1))
    dispute=_dm(core,"pixel-dispute",text="Você errou na análise da imagem anterior.")
    result=await service._incoming_visual_observation(dispute,decision)
    assert result["historical_disputed"] and result["historical"]["status"]=="unavailable"
    assert result["reinspection"]["description"]=="Fresh observation 3"
    assert result["status"]=="partial"
    assert visual_context_for_request(result,remote=True,private=True,mode="reply")
    clock.advance(timedelta(seconds=1))
    result=await service._incoming_visual_observation(
        inbound("pixel-current","Compare com a imagem anterior.",b),decision)
    assert result["historical_current_pixels"]["changed_fraction"]==1
    assert result["historical_disputed"]
    assert visual_context_for_request(result,remote=True,private=True,mode="reply")
    assert len(calls)==4
    clock.advance(timedelta(seconds=1))
    # Restart loses bytes, not historical textual evidence.
    service.visual_previews=VisualPreviewCache(clock)
    result=await service._incoming_visual_observation(
        _dm(core,"pixel-restart",text="Reanalise a imagem anterior."),decision)
    assert "reinspection" not in result and len(calls)==4


@pytest.mark.asyncio
async def test_run04_versioned_fact_atomic_supersession_and_stale_write(lab, monkeypatch):
    core,clock,_=lab
    _bind_bk(core)
    monkeypatch.setenv("AMORA_REFLECTION_REMOTE_REUSE","1")
    original=_dm(core,"version-old",text="Guarde na memória: fato Vexora.cor = dourada")
    out=(await core.messaging.receive(original)).outbound[0]
    old=out.metadata["explicit_memory_id"]
    clock.advance(timedelta(seconds=2))
    nextmsg=_dm(core,"version-new",text="Guarde na memória: fato Vexora.cor = vermelha")
    out=(await core.messaging.receive(nextmsg)).outbound[0]
    new=out.metadata["explicit_memory_id"]
    assert new!=old and core.memory.get(old).archived_at is not None
    assert core.memory.get(new).metadata["supersedes_memory_id"]==old
    late=_dm(core,"version-stale",text="Guarde na memória: fato Vexora.cor = azul").model_copy(update={"timestamp":original.timestamp})
    rejected=(await core.messaging.receive(late)).outbound[0]
    assert rejected.metadata["cognitive_reason_codes"]==["EXPLICIT_MEMORY_VERSION_CONFLICT"]
    assert core.memory.get(new).archived_at is None
    assert (await core.messaging.receive(nextmsg)).outbound[0].metadata["explicit_memory_id"]==new


@pytest.mark.asyncio
async def test_run04_causal_fence_retries_after_late_correction_before_commit(lab, monkeypatch):
    core,clock,_=lab
    runtime=await _start_runtime(core)
    service=core.messaging
    provider=CountingStructuredProvider(name="run04-commit-fence")
    calls=[]
    async def realize(context):
        calls.append(list(context.causal_followups))
        text="Godot para Android." if context.causal_followups else "Godot para PC."
        return {"moves":[{"kind":context.dialogue_plan["response_moves"][0],"text":text}],
                "closing":"statement","_usage":{"input_tokens":12,"output_tokens":4,"status_code":200}}
    provider.realize=realize
    core.cognition.register_provider(provider)
    first=_dm(core,"run04-fence-first",text="Explique Godot para PC.")
    turn=service._turn_id(first)
    original_call=runtime.call
    inserted=False
    second=None
    async def interpose(operation,*args,**kwargs):
        nonlocal inserted,second
        if operation=="finalize" and not inserted:
            inserted=True
            clock.advance(timedelta(seconds=1))
            second=_dm(core,"run04-fence-second",text="Na verdade, Godot para Android.")
            accepted=await service.receive(second)
            assert "DURABLE_REPLY_ACCEPTED" in accepted.decision.reason_codes
        return await original_call(operation,*args,**kwargs)
    monkeypatch.setattr(runtime,"call",interpose)
    try:
        assert "DURABLE_REPLY_ACCEPTED" in (await service.receive(first)).decision.reason_codes
        assert await runtime.run_once()
        assert (await runtime.call("state",turn))["status"]=="retry_wait"
        assert core.messaging_store.get_outbound(service._outbound_id(first)) is None
        assert not core.cognition.store.list_segments(turn)
        clock.advance(timedelta(seconds=10))
        assert await runtime.run_once()
        assert (await runtime.call("state",turn))["status"]=="ready"
        outbound=core.messaging_store.get_outbound(service._outbound_id(first))
        assert outbound.text=="Godot para Android."
        assert len(calls)==2 and not calls[0] and len(calls[1])==1
        assert (await runtime.call("state",service._turn_id(second)))["status"]=="pending"
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_run04_natural_named_fact_consolidation_and_correction(lab,monkeypatch):
    core,clock,_=lab
    runtime=await _start_runtime(core)
    service=core.messaging
    monkeypatch.setenv("AMORA_REFLECTION_REMOTE_REUSE","1")
    from uuid import uuid5,NAMESPACE_URL
    def memory_id(msg):
        return uuid5(NAMESPACE_URL,"amora:owner-fact:"+str(service._turn_id(msg)))
    try:
        first=_dm(core,"natural-fact-old",text="A cor de Vexora é dourada.")
        assert "DURABLE_REPLY_ACCEPTED" in (await service.receive(first)).decision.reason_codes
        assert await service.reflections.process_once()
        old=core.memory.get(memory_id(first))
        assert old and old.provenance.kind.value=="user_assertion"
        clock.advance(timedelta(seconds=2))
        second=_dm(core,"natural-fact-new",text="Na verdade, a cor de Vexora é vermelha.")
        assert "DURABLE_REPLY_ACCEPTED" in (await service.receive(second)).decision.reason_codes
        assert await service.reflections.process_once()
        new=core.memory.get(memory_id(second))
        assert new and new.metadata["supersedes_memory_id"]==str(old.id)
        assert core.memory.get(old.id).archived_at is not None
        assert new.archived_at is None
        assert not await service.reflections.process_once()
        assert "vermelha" in new.content
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
@pytest.mark.parametrize("invalidate_member",[False,True])
async def test_realtime_opt_in_causal_group_commits_one_outbound(lab,monkeypatch,invalidate_member):
    core,clock,_=lab
    monkeypatch.setenv("AMORA_REALTIME_CAUSAL_GROUPING_ENABLED","1")
    runtime=await _start_runtime(core)
    service=core.messaging
    provider=CountingStructuredProvider(name="realtime-group-test")
    observed=[]
    async def realize(context):
        observed.append(context.causal_followups)
        text="A Vexora continua quadrúpede e agora é vermelha." if context.causal_followups else "Vulkan é uma API gráfica."
        return {"moves":[{"kind":context.dialogue_plan["response_moves"][0],"text":text}],
                "closing":"statement","_usage":{"input_tokens":12,"output_tokens":4,"status_code":200}}
    provider.realize=realize
    core.cognition.register_provider(provider)
    first=_dm(core,"realtime-group-first",text="A criatura Vexora é quadrúpede e dourada.")
    try:
        assert "DURABLE_REPLY_ACCEPTED" in (await service.receive(first)).decision.reason_codes
        clock.advance(timedelta(seconds=1))
        second=_dm(core,"realtime-group-second",text="Na verdade, a cor de Vexora é vermelha.")
        assert "DURABLE_REPLY_ACCEPTED" in (await service.receive(second)).decision.reason_codes
        clock.advance(timedelta(seconds=1))
        third=_dm(core,"realtime-group-third",text="Outra coisa, qual é a diferença entre Vulkan e OpenGL?")
        assert "DURABLE_REPLY_ACCEPTED" in (await service.receive(third)).decision.reason_codes
        assert await runtime.run_once()
        a,b,c=[str(service._turn_id(m)) for m in (first,second,third)]
        assert len(observed)==1 and [x["turn_id"] for x in observed[0]]==[b]
        assert observed[0][0]["independent_reply_still_pending"] is False
        assert (await runtime.call("state",a))["status"]=="ready"
        assert (await runtime.call("state",b))["status"]=="ready"
        assert (await runtime.call("state",c))["status"]=="pending"
        outbound=core.messaging_store.get_outbound(service._outbound_id(first))
        assert outbound.metadata["causal_group_members"]==[b]
        assert outbound.metadata["causal_group_sources"]==[{"turn_id":b,
            "source_message_id":second.native_source_message_id,"transport_revision":1}]
        assert core.messaging_store.get_outbound(service._outbound_id(second)) is None
        for msg in (first,second,third):
            turn=service._turn_id(msg)
            assert core.cognition.store.get_message_by_dedupe(f"cognitive-input:{turn}").raw_text==msg.text
        if invalidate_member:
            service.invalidate_transport(TransportInvalidation(platform=MessagingPlatform.DISCORD,
                account_scope=second.account_scope,conversation_id=second.conversation_id,
                native_message_id=second.native_source_message_id,reason="DISCORD_MESSAGE_DELETED",
                transport_revision=1))
        claimed=core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD,
            account_scope="primary",limit=1)
        if invalidate_member:
            assert not claimed
            assert core.messaging_store.get_outbound(outbound.id).status.value=="cancelled"
            return
        assert len(claimed)==1 and claimed[0].id==outbound.id
        assert core.messaging_store.ack_outbound(outbound.id,sent=True,external_message_id="1"*18)
        await runtime.call("reconcile_outbound")
        assert (await runtime.call("state",a))["status"]=="sent"
        assert (await runtime.call("state",b))["status"]=="sent"
        assert await runtime.run_once()
        assert (await runtime.call("state",c))["status"]=="ready"
        assert len(observed)==2
        assert core.messaging_store.get_outbound(service._outbound_id(third)) is not None
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_realtime_bridge_durable_core_preview_final_same_receipt(lab,tmp_path):
    from amora.cognition.realtime_candidates import CANDIDATE_SINK,CandidateEvent
    from amora.messaging.realtime_receipts import RealtimeReceiptStore
    from amora.messaging.realtime_routes import RealtimeOperation
    from amora.messaging.realtime_bridge import digest
    import sqlite3
    core,clock,_=lab
    runtime=await _start_runtime(core)
    service=core.messaging
    is_pg=core.database.backend.capabilities.name=="postgres-candidate"
    connection=_raw() if is_pg else sqlite3.connect(tmp_path/"realtime-bridge.sqlite3")
    journal=RealtimeReceiptStore(connection,postgres=is_pg)
    journal.install_disposable()
    async def port(operation,*args,**kwargs):
        return getattr(journal,operation)(*args,**kwargs)
    bridge=runtime.configure_realtime(port)
    arrived=asyncio.Event()
    resume=asyncio.Event()
    provider=CountingStructuredProvider(name="realtime-bridge-test")
    async def realize(context):
        sink=CANDIDATE_SINK.get()
        assert sink is not None
        sink(CandidateEvent("attempt-1",1,"candidate","Vulkan oferece controle."))
        arrived.set()
        await resume.wait()
        sink(CandidateEvent("attempt-1",2,"transport_complete"))
        return {"moves":[{"kind":context.dialogue_plan["response_moves"][0],
                          "text":"Vulkan oferece controle. OpenGL simplifica o uso."}],
                "closing":"statement","_usage":{"input_tokens":12,"output_tokens":8,"status_code":200}}
    provider.realize=realize
    core.cognition.register_provider(provider)
    message=_dm(core,"1"*18,text="Explique Vulkan e OpenGL.").model_copy(update={"conversation_id":"2"*18})
    turn=service._turn_id(message)
    task=None
    try:
        assert "DURABLE_REPLY_ACCEPTED" in (await service.receive(message)).decision.reason_codes
        task=asyncio.create_task(runtime.run_once())
        await asyncio.wait_for(arrived.wait(),timeout=10)
        assert (await bridge.next("wrong"))["draft"] is None
        draft=(await bridge.next("primary"))["draft"]
        assert draft["snapshot"]["text"]=="Vulkan oferece controle. ▍"
        assert not core.cognition.store.list_segments(turn)
        base=dict(account="primary",turn_id=turn,binding=draft["binding"],index=0)
        op=RealtimeOperation(action="prepare",**base,seq=draft["snapshot"]["seq"],digest=digest(draft["snapshot"]["text"]))
        prepared=(await bridge.operation(op))["prepared"]
        assert prepared["operation"]=="create"
        assert (await bridge.operation(RealtimeOperation(action="confirm",**base,
            token=prepared["token"],digest=prepared["digest"],message_id="3"*18)))["accepted"]
        resume.set()
        assert await task
        cleanup=(await bridge.next("primary"))["draft"]
        assert cleanup["snapshot"]["status"]=="retrying"
        assert cleanup["snapshot"]["text"]=="Geração interrompida."
        # A cleanup may be prepared and then reauthorized; the reservation
        # itself must not cause a false revocation. No extra POST is allowed.
        clean_op=RealtimeOperation(action="prepare",**base,seq=cleanup["snapshot"]["seq"],
            digest=digest(cleanup["snapshot"]["text"]))
        clean=(await bridge.operation(clean_op))["prepared"]
        assert clean["operation"]=="edit" and clean["message_id"]=="3"*18
        assert (await bridge.operation(clean_op.model_copy(update={"action":"authorize"})))["allowed"]
        assert (await bridge.operation(RealtimeOperation(action="reject",**base,token=clean["token"])))["accepted"]
        oid=service._outbound_id(message)
        assert not core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD,account_scope="primary",limit=1)
        claimed=core.messaging_store.claim_outbound(platform=MessagingPlatform.DISCORD,account_scope="primary",
                                                    limit=1,realtime_delivery=True)
        assert len(claimed)==1 and claimed[0].id==oid and claimed[0].metadata["realtime_v1"] is True
        plan=await bridge.final(oid,"primary")
        final=plan["blocks"][0]["snapshot"]
        assert final["text"]=="Vulkan oferece controle. OpenGL simplifica o uso."
        op=RealtimeOperation(action="prepare",**base,seq=final["seq"],digest=digest(final["text"]),
                             terminal=True,outbound_id=oid)
        edit=(await bridge.operation(op))["prepared"]
        assert edit["operation"]=="edit" and edit["message_id"]=="3"*18
        assert not (await bridge.operation(RealtimeOperation(action="ack",**base,
                                                            outbound_id=oid,message_id="3"*18)))["accepted"]
        assert (await bridge.operation(RealtimeOperation(action="confirm",**base,
            token=edit["token"],digest=edit["digest"],message_id="3"*18)))["accepted"]
        assert (await bridge.final(oid,"primary"))["blocks"]==[{"confirmedMessageId":"3"*18}]
        assert (await bridge.operation(RealtimeOperation(action="ack",**base,
                                                        outbound_id=oid,message_id="3"*18)))["accepted"]
        assert core.messaging_store.get_outbound(oid).status.value=="sent"
        assert all("▍" not in m.raw_text for m in core.cognition.store.list_messages(turn_id=turn))
    finally:
        resume.set()
        if task is not None:
            await asyncio.gather(task,return_exceptions=True)
        await _stop_runtime(core)
        connection.close()


def test_realtime_reviewed_registry_exact_and_explicit_opt_in(monkeypatch):
    from amora.storage.postgres_runtime_realtime import RECEIPT_KEYS,realtime_statements
    from amora.storage.reply_job_rollout import realtime_enabled,REALTIME_APPROVAL,APPROVAL
    pg=realtime_statements()
    assert set(pg)==set(RECEIPT_KEYS.values())
    assert not (set(pg)&set(compiled_domain_registry()))
    for sql,key in RECEIPT_KEYS.items():
        assert pg[key].parameter_count==sql.count("?")==pg[key].postgres_sql.count("%s")
    monkeypatch.setenv("AMORA_REALTIME_MODE","off")
    monkeypatch.delenv("AMORA_REALTIME_APPROVAL",raising=False)
    assert not realtime_enabled()
    monkeypatch.setenv("AMORA_REALTIME_MODE","on")
    monkeypatch.setenv("AMORA_REALTIME_APPROVAL",REALTIME_APPROVAL)
    monkeypatch.setenv("AMORA_DURABLE_REPLY_MODE","off")
    monkeypatch.delenv("AMORA_DURABLE_REPLY_APPROVAL",raising=False)
    with pytest.raises(ValueError,match="REALTIME_EXPLICIT_OPT_IN_REQUIRED"):
        realtime_enabled()
    monkeypatch.setenv("AMORA_DURABLE_REPLY_MODE","accept")
    monkeypatch.setenv("AMORA_DURABLE_REPLY_APPROVAL",APPROVAL)
    assert realtime_enabled()


@pytest.mark.asyncio
async def test_realtime_operational_receipts_startup_and_restart(lab,monkeypatch):
    from amora.storage.realtime_receipt_migration import prepare_realtime_schema,APPROVAL as MIGRATE
    from amora.storage.postgres_runtime_realtime import realtime_statements
    from amora.storage.reply_job_rollout import REALTIME_APPROVAL,APPROVAL
    from amora.messaging.realtime_receipts import _DDL,SQLITE_TABLE,binding_digest
    from hashlib import sha256
    core,clock,_=lab
    is_pg=core.database.backend.capabilities.name=="postgres-candidate"
    # The fixture supplies reviewed receipt SQL at backend construction.
    if is_pg:
        with closing(_raw()) as raw:
            with pytest.raises(ValueError,match="OPERATOR_APPROVAL"):
                prepare_realtime_schema(raw,PostgresTargetPolicy(),approval="no")
            assert prepare_realtime_schema(raw,PostgresTargetPolicy(),approval=MIGRATE)["state"]=="prepared"
            assert prepare_realtime_schema(raw,PostgresTargetPolicy(),approval=MIGRATE)["state"]=="prepared"
    else:
        core.database._conn.execute(_DDL.format(table=SQLITE_TABLE))
        core.database._conn.commit()
    monkeypatch.setenv("AMORA_DURABLE_REPLY_MODE","accept")
    monkeypatch.setenv("AMORA_DURABLE_REPLY_APPROVAL",APPROVAL)
    monkeypatch.setenv("AMORA_REALTIME_MODE","on")
    monkeypatch.setenv("AMORA_REALTIME_APPROVAL",REALTIME_APPROVAL)
    runtime=await _start_runtime(core)
    try:
        assert core.messaging.realtime_bridge is not None
        turn=uuid4()
        binding=binding_digest("primary","1"*18,"2"*18,1)
        digest=sha256(b"synthetic").hexdigest()
        call=runtime._operational_receipt
        destination={"account":"primary","channel":"1"*18}
        first=await call("prepare",turn,0,binding=binding,seq=1,digest=digest,**destination)
        assert first.operation=="create"
        assert await call("prepare",turn,0,binding=binding,seq=2,digest=digest,**destination) is None
        await runtime.stop()
        await core.messaging.stop_storage_lane()
        await runtime.start(background=False)
        assert core.messaging.realtime_bridge is not None
        assert (await call("read",turn,0))["pending_token"]==first.token
        # Recover through the reviewed registry using independent nonce evidence.
        assert await call("observe_create",nonce=first.nonce,message_id="3"*18,digest=digest,**destination)
        assert await call("confirm",turn,0,binding=binding,token=first.token,message_id="3"*18,digest=digest)
        second=await call("prepare",turn,0,binding=binding,seq=2,digest=digest,terminal=True,**destination)
        assert second.operation=="edit" and second.message_id=="3"*18
        assert await call("confirm",turn,0,binding=binding,token=second.token,message_id="3"*18,digest=digest)
        assert (await call("read",turn,0))["confirmed_digest"]==digest
    finally:
        await _stop_runtime(core)


@pytest.mark.asyncio
async def test_realtime_orphan_cursor_recovers_after_restart(lab,monkeypatch):
    from amora.storage.realtime_receipt_migration import prepare_realtime_schema,APPROVAL as MIGRATE
    from amora.storage.reply_job_rollout import REALTIME_APPROVAL,APPROVAL
    from amora.messaging.realtime_receipts import _DDL,SQLITE_TABLE,binding_digest,ORPHAN_DIGEST,ORPHAN_TEXT
    from amora.messaging.realtime_routes import RealtimeOperation
    from amora.messaging.realtime_bridge import digest
    core,clock,_=lab
    if core.database.backend.capabilities.name=="postgres-candidate":
        with closing(_raw()) as raw:
            prepare_realtime_schema(raw,PostgresTargetPolicy(),approval=MIGRATE)
    else:
        core.database._conn.execute(_DDL.format(table=SQLITE_TABLE))
        core.database._conn.commit()
    monkeypatch.setenv("AMORA_DURABLE_REPLY_MODE","accept")
    monkeypatch.setenv("AMORA_DURABLE_REPLY_APPROVAL",APPROVAL)
    monkeypatch.setenv("AMORA_REALTIME_MODE","on")
    monkeypatch.setenv("AMORA_REALTIME_APPROVAL",REALTIME_APPROVAL)
    runtime=await _start_runtime(core)
    service=core.messaging
    try:
        message=_dm(core,"1"*18,text="Explique Vulkan.").model_copy(update={"conversation_id":"2"*18})
        turn=service._turn_id(message)
        assert "DURABLE_REPLY_ACCEPTED" in (await service.receive(message)).decision.reason_codes
        claim=await runtime.call("claim")
        assert str(claim.turn_id)==str(turn)
        binding=binding_digest("primary",message.conversation_id,message.native_source_message_id,1)
        call=runtime._operational_receipt
        dest={"account":"primary","channel":message.conversation_id}
        content=digest("Trecho em geração. ▍")
        prepared=await call("prepare",turn,0,binding=binding,seq=1,digest=content,**dest)
        assert await call("confirm",turn,0,binding=binding,token=prepared.token,message_id="3"*18,digest=content)
        assert await call("orphan_candidates","primary",clock.now().isoformat())==[]
        await runtime.stop()
        await service.stop_storage_lane()
        await runtime.start(background=False)
        bridge=service.realtime_bridge
        assert bridge.sessions=={}
        assert await call("orphan_candidates","primary",clock.now().isoformat())==[]
        # The dead process's lease must expire before neutral cleanup.
        clock.advance(timedelta(seconds=300))
        assert (await bridge.next("other")).get("orphans",[])==[]
        envelope=(await bridge.next("primary"))["orphans"][0]
        assert envelope["snapshot"]["text"]==ORPHAN_TEXT
        assert envelope["snapshot"]["status"]=="retrying"
        assert envelope["snapshot"]["seq"]==2
        base=dict(account="primary",turn_id=turn,binding=binding,index=0,orphan=True)
        op=RealtimeOperation(action="prepare",**base,seq=2,digest=ORPHAN_DIGEST)
        assert not (await bridge.operation(op.model_copy(update={"digest":content})))["allowed"]
        assert not (await bridge.operation(op.model_copy(update={"account":"other"})))["allowed"]
        edit=(await bridge.operation(op))["prepared"]
        assert edit["operation"]=="edit" and edit["message_id"]=="3"*18
        assert (await bridge.operation(op.model_copy(update={"action":"authorize"})))["allowed"]
        assert (await bridge.operation(RealtimeOperation(action="confirm",**base,token=edit["token"],
            digest=ORPHAN_DIGEST,message_id="3"*18)))["accepted"]
        assert await call("orphan_candidates","primary",clock.now().isoformat())==[]
        row=await call("read",turn,0)
        assert row["closed"]==0 and row["confirmed_digest"]==ORPHAN_DIGEST
        # The cleanup never completes the canonical job or records speech.
        assert not core.cognition.store.list_segments(turn)
        assert core.messaging_store.get_outbound(service._outbound_id(message)) is None
        later=await call("prepare",turn,0,binding=binding,seq=3,digest=digest("Resposta final."),
                         terminal=True,**dest)
        assert later.operation=="edit" and later.message_id=="3"*18
    finally:
        await _stop_runtime(core)
