import asyncio
import logging
from uuid import uuid4

import pytest

from amora.messaging.reply_diagnostics import ReplyDiagnostics
from amora.cognition.provider_quota import ProviderQuotaCoordinator
from amora.time import SimulatedClock


def test_metrics_bounded_denominators_and_no_private_content(caplog):
    caplog.set_level(logging.INFO, logger="amora.messaging.reply_diagnostics")
    d = ReplyDiagnostics()
    turn = uuid4()
    d.receipt(False)
    d.receipt(True)
    for i in range(300):
        d.attempt(turn, 1, "retry_wait", "PROVIDER_TEMPORARY", i)
    d.attempt(turn, 2, "ready", None, 400)
    d.attempt(turn, 1, "PRIVATE_PAYLOAD", "SECRET_TOKEN", 500)
    s = d.snapshot()
    assert s["receipt_counts"] == {"new_ack":1,"duplicate_ack":1}
    assert s["attempt_outcomes"] == {"retry_wait":300,"ready":1,"unknown":1}
    assert s["recovered_ready_attempts"] == 1
    assert s["attempt_duration_ms"]["sample_count"] == 256
    assert s["attempt_duration_ms"]["p95"] >= s["attempt_duration_ms"]["p50"]
    assert "PRIVATE_PAYLOAD" not in caplog.text and "SECRET_TOKEN" not in caplog.text
    assert "await_transport_ack" in caplog.text


@pytest.mark.asyncio
async def test_cancelled_quota_wait_does_not_leak_lock():
    q = ProviderQuotaCoordinator(SimulatedClock())
    async def waiting():
        async with q.admission("groq",model="same"):
            pass
    async with q.admission("groq",model="same"):
        task = asyncio.create_task(waiting())
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    async with q.admission("groq",model="same",max_wait=.1):
        assert q.snapshot()["groq"]["in_flight"]
    assert not q.snapshot()["groq"]["in_flight"]
