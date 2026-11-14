"""Integrated offline latency and isolation contract for C01-C06.1."""
from __future__ import annotations

import asyncio
import json
import os
import time

import httpx
import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.events import PrivacyScope
from amora.latency_trace import current_trace
from amora.messaging import ChannelType, RoutingAction
from amora.storage.remote import LibSQLBackend
from test_messaging_lifecycle_stability import CountingStructuredProvider, _clock
from test_platform_messaging_step2 import _bind_bk, _message
from test_reflection_v10 import StructuredProvider
from test_remote_storage import HranaSQLite


@pytest.mark.asyncio
async def test_c07_concurrent_dms_during_reflection_have_independent_traces(tmp_path, monkeypatch, caplog, capsys):
    for key in tuple(os.environ):
        if key.startswith("AMORA_SECRET_") or key in {"AMORA_MODE", "AMORA_SOCIAL_PRIMARY"}:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "runtime"))
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    monkeypatch.delenv("AMORA_REFLECTION_REMOTE_REUSE", raising=False)
    caplog.set_level("INFO", logger="amora.performance")
    server = HranaSQLite(tmp_path / "remote.db")
    observed = {}
    measuring = False

    def transport(request):
        trace = current_trace()
        if measuring and trace is not None:
            # Hold the trace object to prevent id reuse across tasks.
            entry = observed.setdefault(id(trace), [trace, 0])
            entry[1] += 1
            time.sleep(0.005)
        return server(request)

    core = AmoraCore.bootstrap(
        clock=_clock(),
        storage=LibSQLBackend("https://database.example", "synthetic-only",
                              transport=httpx.MockTransport(transport)),
    )
    entered = asyncio.Event()
    release = asyncio.Event()

    class GatedReflection(StructuredProvider):
        async def propose(self, context):
            entered.set()
            await release.wait()
            return await super().propose(context)

    social = CountingStructuredProvider(name="c07-offline", delay=0.02)
    reflection = None
    try:
        _bind_bk(core)
        core.cognition.register_provider(social)
        core.cognition.register_provider(GatedReflection())
        seed = CognitiveRequest(
            text="Estou trabalhando em um jogo novo.",
            speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
            session_id="c07-reflection-only",
            privacy_scope=PrivacyScope.PERSONAL, remote_processing_allowed=True,
            metadata={"surface": "platform_messaging", "channel_type": "dm",
                      "memory_consolidation": True},
        )
        core.cognition.observe_input(seed)
        queue = core.messaging.reflections
        assert queue.enqueue(seed)
        caplog.clear()
        measuring = True
        reflection = asyncio.create_task(queue.process_once())
        await asyncio.wait_for(entered.wait(), 10)
        inputs = [
            _message(core, message_id=f"c07-{i}", actor_id="111111111111111111",
                     channel_type=ChannelType.DM, conversation_id=f"c07-channel-{i}",
                     text="Me conta uma coisa engraçada sobre programação.").model_copy(
                         update={"remote_processing_allowed": True})
            for i in range(2)
        ]
        replies = await asyncio.wait_for(
            asyncio.gather(*(core.messaging.receive(message) for message in inputs)), 20)
        assert not reflection.done(), "dialogue must finish without waiting for reflection"
        assert social.calls == 2
        assert all(reply.decision.action == RoutingAction.RESPOND for reply in replies)
        assert all(len(reply.outbound) == 1 for reply in replies)
        assert all(reply.outbound[0].text == "Resposta remota estável." for reply in replies)
        assert all(not reply.outbound[0].metadata["fallback_used"] for reply in replies)
        assert len({reply.outbound[0].id for reply in replies}) == 2
        assert all(not core.messaging.progress_active(core.messaging._turn_id(message)) for message in inputs)
        release.set()
        assert await asyncio.wait_for(reflection, 10)
        assert queue.state(seed.turn_id)["status"] == "done"
        assert core.memory.store.count() >= 1
        measuring = False
        lines = [record.getMessage() for record in caplog.records
                 if record.name == "amora.performance"
                 and ("kind=messaging " in record.getMessage() or "kind=reflection " in record.getMessage())]
        assert len(lines) == 3
        rows = []
        for line in lines:
            fields, metrics = line.split(" metrics=", 1)
            row = dict(field.split("=", 1) for field in fields.split()[1:])
            row["metrics"] = json.loads(metrics)
            rows.append(row)
        dialog = [row for row in rows if row["kind"] == "messaging"]
        reflected = [row for row in rows if row["kind"] == "reflection"]
        assert {row["turn_id"] for row in dialog} == {
            str(reply.outbound[0].metadata["cognitive_turn_id"]) for reply in replies}
        assert reflected[0]["turn_id"] == str(seed.turn_id)
        assert len(observed) == 3
        assert sorted(row["metrics"]["remote_sql_calls"] for row in rows) == sorted(
            count for _, count in observed.values())
        assert all(row["metrics"]["remote_sql_calls"] > 0 for row in rows)
        assert all(row["metrics"]["turn_total_ms"] > 0 for row in dialog)
        assert reflected[0]["metrics"]["reflection_total_ms"] > 0
        with capsys.disabled():
            print("C07_CONCURRENCY_JSON=" + json.dumps(rows, sort_keys=True, separators=(",", ":")), flush=True)
    finally:
        release.set()
        if reflection is not None:
            await asyncio.gather(reflection, return_exceptions=True)
        core.close()
    assert not server.streams
