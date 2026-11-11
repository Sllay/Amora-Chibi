"""Durable messaging reflection: privacy, leases, retries and delivery isolation."""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.cognition.proposals import CognitiveProposalBundle
from amora.events import PrivacyScope
from amora.messaging.reflection_queue import ReflectionQueue
from amora.time import SimulatedClock


def _core(tmp_path: Path, monkeypatch, *, clock=None) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap(clock=clock or SimulatedClock())


def _request(*, privacy=PrivacyScope.PERSONAL, allowed=True) -> CognitiveRequest:
    return CognitiveRequest(
        text="Lembra que comecei um jogo novo chamado Horizontes.",
        speaker_id="PERSON_BK",
        privacy_scope=privacy,
        remote_processing_allowed=allowed,
        metadata={"memory_consolidation": True, "surface": "platform_messaging"},
    )


def test_reflection_queue_is_idempotent_and_private(tmp_path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        queue = ReflectionQueue(core.database, core.clock)
        request = _request()
        assert queue.enqueue(request)
        assert not queue.enqueue(request)
        assert not queue.enqueue(_request(allowed=False))
        assert not queue.enqueue(_request(privacy=PrivacyScope.AUTH))
        assert not queue.enqueue(_request(privacy=PrivacyScope.SECRET))
        assert queue.counts() == {"pending": 1}
        job = queue.claim()
        assert job is not None
        assert job.request.turn_id == request.turn_id
        assert job.request.text == request.text
        assert queue.claim() is None
        assert queue.settle(job)
        assert queue.counts() == {"completed": 1}
        with core.database._lock:
            row = core.database._conn.execute(
                "SELECT request_json FROM messaging_reflection_jobs WHERE turn_id=?",
                (str(request.turn_id),),
            ).fetchone()
        assert row["request_json"] is None
        assert not queue.enqueue(request)  # completed turns never rerun
    finally:
        core.close()


def test_reflection_lease_recovers_after_core_restart(tmp_path, monkeypatch) -> None:
    clock = SimulatedClock()
    core = _core(tmp_path, monkeypatch, clock=clock)
    request = _request()
    try:
        queue = ReflectionQueue(core.database, clock)
        assert queue.enqueue(request)
        old_claim = queue.claim()
        assert old_claim is not None
        assert old_claim.attempts == 1
    finally:
        core.close()

    restarted = _core(tmp_path, monkeypatch, clock=clock)
    try:
        queue = ReflectionQueue(restarted.database, clock)
        assert queue.claim() is None
        clock.advance(timedelta(seconds=241))
        recovered = queue.claim()
        assert recovered is not None
        assert recovered.attempts == 2
        assert recovered.request.turn_id == request.turn_id
        assert not queue.settle(old_claim)  # stale lease token cannot ack
        assert queue.settle(recovered)
    finally:
        restarted.close()


@pytest.mark.asyncio
async def test_runner_retries_failed_reflection_without_blocking_and_finishes(tmp_path, monkeypatch) -> None:
    clock = SimulatedClock()
    core = _core(tmp_path, monkeypatch, clock=clock)

    class FakeCognition:
        def __init__(self):
            self.calls = 0

        async def reflect(self, request):
            self.calls += 1
            if self.calls == 1:
                return CognitiveProposalBundle(reason_codes=["REFLECTION_PROVIDER_FAILURE"])
            return CognitiveProposalBundle(accepted_memory_ids=["synthetic-memory"])

    try:
        queue = core.messaging.reflection_runner.queue
        fake = FakeCognition()
        runner = core.messaging.reflection_runner
        runner.cognition = fake
        assert queue.enqueue(_request())
        assert await runner.process_one()
        assert queue.counts() == {"retry": 1}
        assert not await runner.process_one()  # must honor backoff
        clock.advance(timedelta(seconds=16))
        assert await runner.process_one()
        assert queue.counts() == {"completed": 1}
        assert fake.calls == 2
        assert not await runner.process_one()
    finally:
        core.close()


@pytest.mark.asyncio
async def test_terminal_provider_failures_shed_payload_after_max_attempts(tmp_path, monkeypatch) -> None:
    clock = SimulatedClock()
    core = _core(tmp_path, monkeypatch, clock=clock)

    class Unavailable:
        async def reflect(self, request):
            return CognitiveProposalBundle(reason_codes=["NO_STRUCTURED_REFLECTION_PROVIDER"])

    try:
        runner = core.messaging.reflection_runner
        runner.cognition = Unavailable()
        request = _request()
        assert runner.queue.enqueue(request)
        for _ in range(5):
            assert await runner.process_one()
            clock.advance(timedelta(seconds=1801))
        assert runner.queue.counts() == {"failed": 1}
        assert not await runner.process_one()
        with core.database._lock:
            row = core.database._conn.execute(
                "SELECT request_json, attempts, last_error_code FROM messaging_reflection_jobs WHERE turn_id=?",
                (str(request.turn_id),),
            ).fetchone()
        assert row["request_json"] is None
        assert int(row["attempts"]) == 5
        assert row["last_error_code"] == "NO_STRUCTURED_REFLECTION_PROVIDER"
    finally:
        core.close()
