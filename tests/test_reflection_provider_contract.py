from __future__ import annotations

import json
from datetime import timedelta

import httpx
import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest, JournalStage
from amora.cognition.providers_identity import GroqCognitiveProvider, GeminiCognitiveProvider
from amora.cognition.providers_openrouter import OpenRouterCognitiveProvider
from amora.cognition.reflection_contract import (
    MEMORY_ONLY_REASON, ReflectionSchemaError, memory_schema, safe_reflection_failure,
    validate_memory_extraction,
)
from amora.events import PrivacyScope
from amora.memory import MemoryQuery
from amora.time import SimulatedClock


SOURCE = "Meu projeto fictício Orquidea terá cinco mapas."
MARKER = "SYNTHETIC_UNRELATED_PRIVATE_HISTORY"


class Secrets:
    def get(self, name):
        return "synthetic-token" if name in {"GROQ_API_KEY", "GEMINI_API_KEY", "OPENROUTER_API_KEY"} else None


def item():
    return {
        "kind": "episodic", "content": "O falante relatou que o projeto Orquidea terá cinco mapas.",
        "summary": "Projeto Orquidea: cinco mapas",
        "confidence": .95, "importance": .95, "novelty": .95, "emotional_weight": .3,
        "relationship_relevance": .4, "future_consequence": .9, "goal_relevance": .9,
        "rarity": .3, "repetition": 0.,
    }


def response_object(raw):
    return {"status": "completed", "output": [{"type": "message", "content": [
        {"type": "output_text", "text": json.dumps(raw)}]}],
        "usage": {"input_tokens": 100, "output_tokens": 70}}


def request():
    return CognitiveRequest(
        text=SOURCE, speaker_id="PERSON_BK", target_id="ENTITY_AMORA",
        session_id="private-synthetic", privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
        metadata={"surface": "platform_messaging", "channel_type": "dm", "memory_consolidation": True},
    )


def context_for(core):
    req = request()
    req.metadata.update({"cognitive_mode": "reflection", "durable_memory_reflection": True})
    return core.cognition.compiler.compile(req)


def fake_http(monkeypatch, handler):
    original = httpx.AsyncClient
    def factory(*args, **kwargs):
        return original(*args, **kwargs, transport=httpx.MockTransport(handler))
    monkeypatch.setattr(httpx, "AsyncClient", factory)


def test_memory_schema_matches_validation_and_does_not_accept_fabricated_state():
    schema = memory_schema()
    assert schema["properties"]["memory_candidates"]["maxItems"] == 3
    assert set(schema["properties"]["memory_candidates"]["items"]["required"]) == set(item())
    assert validate_memory_extraction({"memory_candidates": [item()]})["memory_candidates"]
    assert validate_memory_extraction({"memory_candidates": []}) == {"memory_candidates": []}
    for invalid in ({}, {"memory_candidates": [dict(item(), confidence=1.2)]},
                    {"memory_candidates": [dict(item(), confidence="0.8")]},
                    {"memory_candidates": [dict(item(), content="   ")]},
                    {"memory_candidates": [], "accepted_memory_ids": ["untrusted"]},
                    {"memory_candidates": [item()] * 4}):
        with pytest.raises(ReflectionSchemaError):
            validate_memory_extraction(invalid)
    with pytest.raises(ReflectionSchemaError) as raised:
        validate_memory_extraction({"memory_candidates": [dict(item(), **{MARKER: MARKER})]})
    assert MARKER not in json.dumps(safe_reflection_failure(raised.value))


@pytest.mark.asyncio
async def test_groq_production_responses_schema_repair_and_durable_write(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_REFLECTION_REMOTE_REUSE", "1")
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    payloads = []
    try:
        provider = GroqCognitiveProvider(Secrets())
        core.cognition.register_provider(provider)
        # Remove any optional real fallback; all requests are synthetic.
        def handler(req):
            assert req.url.host == "api.groq.com"
            assert req.url.path.endswith("/responses")
            payload = json.loads(req.content)
            payloads.append(payload)
            if len(payloads) == 1:
                return httpx.Response(200, json=response_object({"memory_candidates": [dict(item(), confidence=2)]}))
            return httpx.Response(200, json=response_object({"memory_candidates": [item()]}))
        fake_http(monkeypatch, handler)
        req = request()
        old = req.model_copy(update={"text": MARKER, "turn_id": __import__("uuid").uuid4()})
        core.cognition.observe_input(old)
        core.cognition.observe_input(req)
        queue = core.messaging.reflections
        assert queue.enqueue(req)
        assert await queue.process_once()
        assert queue.state(req.turn_id)["status"] == "done"
        assert queue.state(req.turn_id)["last_reason"] == "MEMORY_REFLECTION_ACCEPTED"
        assert len(payloads) == 2
        for payload in payloads:
            assert payload["model"] == "qwen/qwen3.8-27b"
            assert payload["text"]["format"]["type"] == "json_schema"
            assert payload["text"]["format"]["schema"] == memory_schema()
            assert payload["reasoning"] == {"effort": "none"}
            assert payload["store"] is False
            assert len(payload["input"]) == 1
            assert SOURCE in payload["input"][0]["content"]
            assert MARKER not in json.dumps(payload)
        assert "Corrija o formato" in payloads[1]["instructions"]
        hit = next(h.memory for h in core.memory.recall(MemoryQuery(text="Orquidea"))
                   if "Orquidea" in h.memory.content)
        assert hit.owner_entity_id == "PERSON_BK"
        assert hit.remote_processing_allowed
        assert hit.metadata["turn_id"] == str(req.turn_id)
        gated = next(j for j in core.cognition.store.journal_for_turn(req.turn_id)
                     if j.stage == JournalStage.PROPOSALS_GATED)
        assert gated.payload["accepted_memory_ids"] == [str(hit.id)]
        assert gated.payload["hypothesis_ids"] == []
        assert gated.payload["deferred_action_count"] == 0
    finally:
        core.close()


@pytest.mark.asyncio
async def test_reflection_http_429_cooldown_does_not_disable_social_groq(tmp_path, monkeypatch, caplog):
    from amora.models import TaskClass
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    calls = []
    try:
        provider = GroqCognitiveProvider(Secrets())
        core.cognition.register_provider(provider)
        # Ensure only this provider participates in the failure test.
        for name in list(core.cognition._providers):
            if name != "groq":
                core.cognition._providers.pop(name)
                core.cognition.router.register(core.cognition.router.descriptor(name).model_copy(update={"available": False})
                                               if hasattr(core.cognition.router.descriptor(name), "model_copy")
                                               else __import__("dataclasses").replace(core.cognition.router.descriptor(name), available=False))
        def handler(req):
            calls.append(json.loads(req.content))
            return httpx.Response(429, headers={"retry-after": "90"},
                                  json={"error": {"message": MARKER}})
        fake_http(monkeypatch, handler)
        req = request()
        core.cognition.observe_input(req)
        queue = core.messaging.reflections
        assert queue.enqueue(req)
        assert await queue.process_once()
        assert len(calls) == 1
        assert queue.state(req.turn_id)["status"] == "pending"
        state = core.cognition.state()
        assert state["cognitive_reflection_provider_failures"]["groq"] == 1
        assert "groq" not in state["cognitive_provider_failures"]
        assert "groq" not in state["cognitive_provider_cooldowns"]
        assert any(d.name == "groq" for d in core.cognition.router.ranked_candidates(
            TaskClass.SOCIAL_DIALOGUE, privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True, require_structured_output=True))
        errors = [j for j in core.cognition.store.journal_for_turn(req.turn_id) if j.stage == JournalStage.ERROR]
        assert errors[-1].payload["status_code"] == 429
        assert errors[-1].payload["retry_after_seconds"] == 90
        assert "PROVIDER_RATE_LIMIT" in errors[-1].reason_codes
        assert MARKER not in caplog.text and MARKER not in str(errors[-1].model_dump())
        core.clock.advance(timedelta(seconds=15))
        assert await queue.process_once()
        assert len(calls) == 1
        assert queue.state(req.turn_id)["attempts"] == 1
        assert queue.state(req.turn_id)["last_reason"] == "REFLECTION_PROVIDERS_IN_BACKOFF"
    finally:
        core.close()


@pytest.mark.asyncio
async def test_gemini_and_openrouter_extract_only_current_source(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    payloads = []
    try:
        ctx = context_for(core)
        assert MEMORY_ONLY_REASON in ctx.reason_codes and not ctx.recent_messages
        ctx.recent_messages = [{"text": MARKER, "speaker_id": "ENTITY_AMORA"}]
        def handler(req):
            payload = json.loads(req.content)
            payloads.append(payload)
            if "googleapis" in req.url.host:
                return httpx.Response(200, json={"candidates": [{"content": {"parts": [
                    {"text": json.dumps({"memory_candidates": [item()]})}]}}]})
            return httpx.Response(200, json=response_object({"memory_candidates": [item()]}))
        fake_http(monkeypatch, handler)
        for provider in (GeminiCognitiveProvider(Secrets()), OpenRouterCognitiveProvider(Secrets())):
            out = await provider.propose(ctx)
            assert out["memory_candidates"][0]["content"] == item()["content"]
        assert payloads[0]["generationConfig"]["responseJsonSchema"] == memory_schema()
        assert payloads[1]["text"]["format"]["type"] == "json_object"
        assert all(MARKER not in json.dumps(p) and SOURCE in json.dumps(p, ensure_ascii=False) for p in payloads)
    finally:
        core.close()



@pytest.mark.asyncio
async def test_synthetic_operator_probe_requires_useful_memory_and_never_persists(tmp_path, monkeypatch):
    from amora.admin_commands import _command_memory_reflection_probe
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        core.cognition.register_provider(GroqCognitiveProvider(Secrets()))
        results = iter(({"memory_candidates": []}, {"memory_candidates": [item()]}))
        def handler(req):
            return httpx.Response(200, json=response_object(next(results)))
        fake_http(monkeypatch, handler)
        count = core.memory.store.count()
        empty = await _command_memory_reflection_probe(core, [])
        assert empty["data"]["success"] is False
        assert empty["data"]["would_store_count"] == 0
        useful = await _command_memory_reflection_probe(core, [])
        assert useful["data"]["success"] is True
        assert useful["data"]["would_store_count"] == 1
        assert core.memory.store.count() == count
    finally:
        core.close()
