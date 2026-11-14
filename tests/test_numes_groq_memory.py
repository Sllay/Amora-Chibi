from __future__ import annotations

import json
import httpx
import pytest

from amora.app import AmoraCore
from amora.cognition import CognitiveRequest, JournalStage
from amora.cognition.providers_numes import NumesCognitiveProvider
from amora.cognition.providers_memory import GroqMemoryProvider
from amora.cognition.providers import ProviderError
from amora.events import PrivacyScope
from amora.models import TaskClass
from amora.time import SimulatedClock
from test_reflection_provider_contract import (
    fake_http, item, request, response_object, SOURCE, MARKER, context_for,
)


def configure(monkeypatch, tmp_path):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_SOCIAL_PRIMARY", "numes")
    monkeypatch.setenv("AMORA_SECRET_NUMES_API_KEY", "synthetic-numes-key")
    monkeypatch.setenv("AMORA_SECRET_GROQ_API_KEY", "synthetic-groq-key")
    monkeypatch.setenv("AMORA_GROQ_MEMORY_MODEL", "openai/gpt-oss-120b")
    monkeypatch.setenv("AMORA_NUMES_DIALOGUE_MODEL", NumesCognitiveProvider.DEFAULT_MODEL)


def test_opt_in_provider_configuration_and_scope(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        assert core.models.select(TaskClass.SOCIAL_DIALOGUE).name == "numes"
        assert core.models.descriptor("numes").model == NumesCognitiveProvider.DEFAULT_MODEL
        assert core.models.descriptor("groq_memory").model == "openai/gpt-oss-120b"
        assert TaskClass.SOCIAL_DIALOGUE not in core.models.descriptor("groq_memory").capabilities
        assert TaskClass.REASONING not in core.models.descriptor("numes").capabilities
        assert TaskClass.SOCIAL_DIALOGUE not in core.models.descriptor("groq").capabilities
        assert not callable(getattr(core.cognition._providers["groq"], "realize", None))
        assert core.cognition._providers["numes"].max_retries == 0
        assert core.cognition._providers["numes"].timeout_seconds == 90.0
        assert not any(d.name == "numes" for d in core.models.ranked_candidates(
            TaskClass.SOCIAL_DIALOGUE, privacy_scope=PrivacyScope.SECRET))
        assert not any(d.name == "numes" for d in core.models.ranked_candidates(
            TaskClass.SOCIAL_DIALOGUE, privacy_scope=PrivacyScope.PRIVATE))
    finally:
        core.close()


@pytest.mark.asyncio
async def test_numes_chat_contract_and_zero_token_price(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        ctx = core.cognition.compiler.compile(CognitiveRequest(
            text="Estou criando um jogo novo.", speaker_id="PERSON_BK",
            remote_processing_allowed=True, session_id="synthetic"))
        # A caller's alternate expression profile cannot load an external persona.
        ctx = ctx.model_copy(update={"expression_profile": "amora-v3"})
        payloads = []
        def handler(req):
            assert req.url.host == "api.numes.ai"
            assert req.url.path == "/api/v1/chat/completions"
            assert req.headers["authorization"] == "Bearer synthetic-numes-key"
            payload = json.loads(req.content)
            payloads.append(payload)
            kind = ctx.dialogue_plan["response_moves"][0]
            return httpx.Response(200, json={
                "choices": [{"message": {"content": json.dumps({
                    "moves": [{"kind": kind, "text": "Que legal, um jogo novo!"}],
                    "closing": "statement"}), "reasoning_content": "not a final reply"},
                    "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 100, "completion_tokens": 20, "cost": 0}})
        fake_http(monkeypatch, handler)
        provider = NumesCognitiveProvider(core.secrets)
        result = await provider.realize(ctx)
        assert result["moves"][0]["text"] == "Que legal, um jogo novo!"
        payload = payloads[0]
        assert payload["store"] is False
        assert payload["model"] == NumesCognitiveProvider.DEFAULT_MODEL
        assert payload["provider"]["max_price"] == {"prompt": 0, "completion": 0}
        assert payload["provider"]["zdr"] is True
        assert payload["provider"]["data_collection"] == "deny"
        assert payload["response_format"] == {"type": "json_object"}
        assert "Estou criando um jogo novo." in payload["messages"][-1]["content"]
        assert "synthetic-groq-key" not in json.dumps(payload)
        assert "synthetic-numes-key" not in json.dumps(payload)
        assert ctx.expression_profile == "amora-v3"
        with pytest.raises(ProviderError):
            await provider.realize(ctx.model_copy(update={"remote_processing_allowed": False}))
        assert len(payloads) == 1
    finally:
        core.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [413, 429, 503])
async def test_numes_errors_are_safe_and_keep_status(tmp_path, monkeypatch, status):
    configure(monkeypatch, tmp_path)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        ctx = core.cognition.compiler.compile(CognitiveRequest(
            text="Oi Amora", speaker_id="PERSON_BK", remote_processing_allowed=True))
        fake_http(monkeypatch, lambda req: httpx.Response(status,
            headers={"retry-after": "60"}, json={"error": {"message": MARKER}}))
        with pytest.raises(ProviderError) as raised:
            await NumesCognitiveProvider(core.secrets).realize(ctx)
        assert raised.value.status_code == status
        assert raised.value.retry_after == "60"
        assert MARKER not in str(raised.value)
    finally:
        core.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_primary", [False, True])
async def test_gpt_oss_memory_queue_and_qwen_fallback(tmp_path, monkeypatch, fail_primary):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("AMORA_GROQ_DIALOGUE_MODEL", "qwen/qwen3.8-27b")
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    calls = []
    try:
        def handler(req):
            assert req.url.host == "api.groq.com"
            payload = json.loads(req.content)
            calls.append(payload)
            assert SOURCE in payload["input"][0]["content"]
            assert MARKER not in json.dumps(payload)
            assert payload["store"] is False
            assert len(payload["input"]) == 1
            assert set(json.loads(payload["input"][0]["content"])) == {"speaker_id", "addressee_id", "message"}
            assert "não a personagem" in payload["instructions"]
            assert "AMORA REALIZATION CONTRACT" not in payload["instructions"]
            if fail_primary and payload["model"] == "openai/gpt-oss-120b":
                return httpx.Response(429, headers={"retry-after": "60"})
            return httpx.Response(200, json=response_object({"memory_candidates": [item()]}))
        fake_http(monkeypatch, handler)
        req = request()
        core.cognition.observe_input(req)
        queue = core.messaging.reflections
        assert queue.enqueue(req)
        assert await queue.process_once()
        assert queue.state(req.turn_id)["status"] == "done"
        assert calls[0]["model"] == "openai/gpt-oss-120b"
        assert calls[0]["reasoning"] == {"effort": "low"}
        assert calls[0]["text"]["format"]["type"] == "json_schema"
        if fail_primary:
            assert [p["model"] for p in calls] == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"]
        else:
            assert len(calls) == 1
        entry = next(j for j in core.cognition.store.journal_for_turn(req.turn_id)
                     if j.stage == JournalStage.PROPOSALS_GATED)
        assert entry.payload["accepted_memory_ids"]
        assert core.models.select(TaskClass.SOCIAL_DIALOGUE).name == "numes"
    finally:
        core.close()


@pytest.mark.asyncio
async def test_memory_probe_uses_configured_specialist(tmp_path, monkeypatch):
    from amora.admin_commands import _command_memory_reflection_probe
    configure(monkeypatch, tmp_path)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        def handler(req):
            payload = json.loads(req.content)
            assert payload["model"] == "openai/gpt-oss-120b"
            return httpx.Response(200, json=response_object({"memory_candidates": [item()]}))
        fake_http(monkeypatch, handler)
        before = core.memory.store.count()
        result = await _command_memory_reflection_probe(core, [])
        assert result["data"]["success"] is True
        assert result["data"]["model"] == "openai/gpt-oss-120b"
        assert core.memory.store.count() == before
    finally:
        core.close()


@pytest.mark.asyncio
async def test_numes_social_failure_does_not_route_to_groq(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("AMORA_SECRET_GEMINI_API_KEY", "synthetic-gemini-key")
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        req = CognitiveRequest(text="Estou criando um jogo novo.", speaker_id="PERSON_BK",
                               remote_processing_allowed=True, session_id="synthetic")
        ctx = core.cognition.compiler.compile(req)
        called = []
        async def numes(context):
            called.append("numes")
            raise ProviderError("synthetic unavailable", status_code=503)
        async def gemini(context):
            called.append("gemini")
            return {"moves": [{"kind": context.dialogue_plan["response_moves"][0],
                               "text": "Que legal, um jogo novo!"}], "closing": "statement"}
        monkeypatch.setattr(core.cognition._providers["numes"], "realize", numes)
        monkeypatch.setattr(core.cognition._providers["gemini"], "realize", gemini)
        # The production router, not a hand-authored provider order.
        ranked = core.models.ranked_candidates(TaskClass.SOCIAL_DIALOGUE,
                                               require_structured_output=True)
        assert ranked[0].name == "numes"
        assert "groq" not in [d.name for d in ranked]
        assert "groq_memory" not in [d.name for d in ranked]
        plan = await core.cognition.respond(req)
        assert plan.provider == "gemini"
        assert called == ["numes", "gemini"]
    finally:
        core.close()


def test_payload_metrics_contains_only_numeric_metadata(caplog):
    from amora.cognition.payload_metrics import log_payload_metrics
    import logging
    payload = {"model": "synthetic", "input": [{"role": "user", "content": MARKER}],
               "max_output_tokens": 1200}
    with caplog.at_level(logging.INFO, logger="amora.cognition.payload"):
        log_payload_metrics("groq", "https://api.groq.com/openai/v1/responses", payload,
                            phase="memory_extraction")
    record = caplog.records[-1]
    assert record.body_bytes == len(httpx.Request("POST", "https://api.groq.com/openai/v1/responses",
                                                   json=payload).content)
    assert record.message_count == 1 and record.source_only is True
    assert record.max_output_tokens == 1200
    assert MARKER not in str(record.__dict__)
