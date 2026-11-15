"""PCS.1: same durable fixture, local SQLite vs current remote Core.

No live providers, credentials, production DB or new backend selection.
The local arm is an isolated restored snapshot, NOT a synchronized replica.
"""
from __future__ import annotations

import hashlib
import json
import os
import statistics
import time
from uuid import NAMESPACE_URL, uuid5

import httpx
import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.cognition.expressive_profile import V3_SHA256
from amora.cognition.prompt_frame import render_trusted_protocol
from amora.cognition.providers_numes import NumesCognitiveProvider
from amora.events import PrivacyScope
from amora.messaging import ChannelType, RoutingAction
from amora.settings import RuntimePaths
from amora.storage import AmoraDatabase
from amora.storage.backend import SQLiteBackend
from amora.storage.remote import LibSQLBackend
from test_memory_v03 import important_candidate
from test_messaging_lifecycle_stability import CountingStructuredProvider, _clock
from test_platform_messaging_step2 import _bind_bk, _message
from test_remote_storage import HranaSQLite


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode()).hexdigest()


def request(text, index):
    return CognitiveRequest(
        turn_id=uuid5(NAMESPACE_URL, f"pcs1-lab-{index}"),
        text=text, speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
        session_id="pcs1-private", privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
        metadata={"surface": "platform_messaging", "channel_type": "dm"},
    )


def summarize(rows):
    return {
        key: {"median": round(statistics.median(row[key] for row in rows), 3),
              "max": round(max(row[key] for row in rows), 3)}
        for key in ("wall_ms", "sql_calls")
    }


@pytest.mark.asyncio
async def test_pcs1_same_snapshot_context_payload_and_turn_latency(tmp_path, monkeypatch, capsys):
    for key in tuple(os.environ):
        if key.startswith("AMORA_SECRET_") or key in {
            "AMORA_MODE", "AMORA_SOCIAL_PRIMARY", "AMORA_HOME",
            "AMORA_STORAGE_BACKEND", "AMORA_SOCIAL_EXPRESSION_PROFILE",
        }:
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    monkeypatch.setenv("AMORA_SOCIAL_EXPRESSION_PROFILE", "amora-v3")
    server = HranaSQLite(tmp_path / "primary.db")
    delay = 0.0
    calls = 0

    def transport(http_request):
        nonlocal calls
        calls += 1
        if delay:
            time.sleep(delay)
        return server(http_request)

    backend = LibSQLBackend("https://database.example", "fixture-only",
                            transport=httpx.MockTransport(transport))
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "seed"))
    seed = AmoraCore.bootstrap(clock=_clock(), storage=backend)
    try:
        _bind_bk(seed)
        for index in range(48):
            assert seed.memory.remember(important_candidate(
                content=f"Projeto sintético aurora: desenho e programação, item {index}.",
                privacy_scope=PrivacyScope.PERSONAL,
                owner_entity_id="PERSON_BK",
            )).memory is not None
        assert seed.memory.remember(important_candidate(
            content="marcador-local-privado-never-remote",
            privacy_scope=PrivacyScope.PRIVATE, owner_entity_id="PERSON_BK",
        )).memory is not None
        for index in range(8):
            seed.cognition.observe_input(request(
                f"Histórico sintético: desenho aurora, etapa {index}.", -index - 1))
        snapshot = seed.create_backup("pcs1-fixture.db")
    finally:
        seed.close()

    # Both arms start from exactly the same durable records and identifiers.
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "local"))
    AmoraDatabase.restore_backup(snapshot, RuntimePaths.from_environment().database)
    prompts = (
        "Amora, lembra do desenho aurora?",
        "Minha amiga falou com a sua mãe. Quem falou com quem?",
        "O que você acha da programação do projeto aurora?",
    )
    contexts = {}
    payloads = {}
    summaries = {}
    turns = {}

    async def payload_digest(context):
        provider = NumesCognitiveProvider({"NUMES_API_KEY": "fixture-only"})
        captured = []
        async def capture(payload, *, key, phase):
            assert key == "fixture-only"
            assert phase == "realization"
            captured.append(digest(payload))
            # Do not dump the payload on assertion failure or in CI.
            return {}
        provider._complete_stream = capture
        await provider.realize(context)
        assert len(captured) == 1
        return captured[0]

    for name, storage in (("remote_hrana_20ms", backend), ("local_sqlite", SQLiteBackend())):
        monkeypatch.setenv("AMORA_HOME", str(tmp_path / ("remote-run" if name.startswith("remote") else "local")))
        core = AmoraCore.bootstrap(clock=_clock(), storage=storage)
        store = core.cognition.store
        provider = CountingStructuredProvider(name="pcs1-offline")
        core.cognition.register_provider(provider)
        store.start_async_lane()
        core.messaging.start_storage_lane()
        try:
            contexts[name], payloads[name] = [], []
            measurements = []
            for index, prompt in enumerate(prompts):
                req = request(prompt, index)
                calls = 0
                delay = .020 if name.startswith("remote") else 0
                started = time.perf_counter()
                context = await core.cognition.compiler.compile_async(req)
                elapsed = (time.perf_counter() - started) * 1000
                delay = 0
                measurements.append({"wall_ms": elapsed, "sql_calls": calls})
                assert context.expression_profile == "amora-v3"
                assert "marcador-local-privado-never-remote" not in context.model_dump_json()
                # Full context equality (including ACIR, memories and roles).
                # Hash only: never expose profile contents in CI logs.
                contexts[name].append(digest(context.model_dump(mode="json")))
                payloads[name].append((
                    digest(render_trusted_protocol(context)),
                    await payload_digest(context),
                ))
            summaries[name] = summarize(measurements)

            # Separate real messaging path comparison. Same synthetic provider,
            # source revision, session and texts. No reflection worker or LLM.
            measurements = []
            for index, prompt in enumerate(prompts):
                msg = _message(core, message_id=f"pcs1-turn-{index}",
                               actor_id="111111111111111111", channel_type=ChannelType.DM,
                               conversation_id="pcs1-dm", text=prompt).model_copy(
                                   update={"remote_processing_allowed": True})
                calls = 0
                delay = .020 if name.startswith("remote") else 0
                started = time.perf_counter()
                result = await core.messaging.receive(msg)
                elapsed = (time.perf_counter() - started) * 1000
                delay = 0
                measurements.append({"wall_ms": elapsed, "sql_calls": calls})
                assert result.decision.action == RoutingAction.RESPOND
                assert len(result.outbound) == 1
                assert result.outbound[0].text == "Resposta remota estável."
                assert not result.outbound[0].metadata["fallback_used"]
                retry = await core.messaging.receive(msg)
                assert retry.duplicate and retry.outbound[0].id == result.outbound[0].id
                assert provider.calls == index + 1
            turns[name] = summarize(measurements)
        finally:
            delay = 0
            await core.messaging.stop_inflight()
            await core.messaging.stop_storage_lane()
            await store.stop_async_lane()
            core.close()

    assert contexts["remote_hrana_20ms"] == contexts["local_sqlite"], "context digest mismatch"
    assert payloads["remote_hrana_20ms"] == payloads["local_sqlite"], "provider payload digest mismatch"
    assert summaries["remote_hrana_20ms"]["sql_calls"]["median"] > 0
    assert summaries["local_sqlite"]["sql_calls"]["max"] == 0
    assert turns["remote_hrana_20ms"]["sql_calls"]["median"] > 0
    assert turns["local_sqlite"]["sql_calls"]["max"] == 0
    assert not server.streams
    report = {
        "profile_sha256": V3_SHA256, "fixture_db_bytes": snapshot.stat().st_size,
        "context_samples_per_arm": len(prompts), "context": summaries,
        "turn_samples_per_arm": len(prompts), "messaging": turns,
        "equivalent_contexts": len(prompts), "equivalent_payloads": len(prompts),
        "synthetic_pipeline_delay_ms": 20,
    }
    with capsys.disabled():
        print("PCS1_LAB_JSON=" + json.dumps(report, sort_keys=True), flush=True)
