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


def test_numes_free_route_public_catalog_id(monkeypatch):
    monkeypatch.delenv("AMORA_NUMES_DIALOGUE_MODEL", raising=False)
    assert NumesCognitiveProvider.DEFAULT_MODEL == "dealignai/deepseek-v4.1-flash-uncensored-fp8:free"
    assert NumesCognitiveProvider.DEFAULT_MODEL.endswith(":free")
    # A deployed Northflank environment may still contain this legacy value.
    monkeypatch.setenv(
        "AMORA_NUMES_DIALOGUE_MODEL",
        "dealignai/deepseek-v4.1-flash-uncensored-fp8",
    )
    class Secrets:
        def get(self, name):
            return "fictional-only"
    provider = NumesCognitiveProvider(Secrets())
    assert provider.model == NumesCognitiveProvider.DEFAULT_MODEL


def test_opt_in_provider_configuration_and_scope(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        assert core.models.select(TaskClass.SOCIAL_DIALOGUE).name == "numes"
        assert core.models.descriptor("numes").model == NumesCognitiveProvider.DEFAULT_MODEL
        assert core.models.descriptor("groq_memory").model == "openai/gpt-oss-120b"
        assert TaskClass.SOCIAL_DIALOGUE not in core.models.descriptor("groq_memory").capabilities
        assert TaskClass.REASONING not in core.models.descriptor("numes").capabilities
        assert TaskClass.SOCIAL_DIALOGUE in core.models.descriptor("groq").capabilities
        assert core.models.descriptor("groq").model == "qwen/qwen3.8-27b"
        assert callable(core.cognition._providers["groq"].realize)
        assert core.models.descriptor("groq_memory").model == "openai/gpt-oss-120b"
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
            return sse_reply({"moves": [{"kind": kind, "text": "Que legal, um jogo novo!"}],
                              "closing": "statement"})
        fake_http(monkeypatch, handler)
        provider = NumesCognitiveProvider(core.secrets)
        result = await provider.realize(ctx)
        assert result["moves"][0]["text"] == "Que legal, um jogo novo!"
        payload = payloads[0]
        assert payload["store"] is False
        assert payload["stream"] is True
        assert result["_usage"]["transport"] == "sse"
        assert result["_usage"]["first_content_ms"] is not None
        assert result["_usage"]["first_reasoning_ms"] is not None
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
async def test_numes_catalogue_audit_is_readonly_and_does_not_assume_zdr(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        calls = []
        def handler(req):
            calls.append(req)
            assert req.method == "GET"
            assert req.url.host == "api.numes.ai"
            assert req.url.path.endswith(
                "/models/dealignai/deepseek-v4.1-flash-uncensored-fp8:free/endpoints"
            )
            assert "synthetic-numes-key" == req.headers["authorization"].split()[-1]
            assert not req.content
            return httpx.Response(200, json={"data": {"endpoints": [
                {"supported_parameters": ["max_tokens", "temperature", "response_format"],
                 "max_completion_tokens": 4096},
                {"supported_parameters": ["max_tokens"],
                 "max_completion_tokens": 512},
            ]}})
        fake_http(monkeypatch, handler)
        result = await NumesCognitiveProvider(core.secrets).audit_endpoint_catalogue()
        assert result == {
            "success": True,
            "http_status": 200,
            "model_free_route": True,
            "endpoint_count": 2,
            "strict_parameter_declared_endpoints": 1,
            "max_output_1200_eligible_endpoints": 1,
            "privacy_policy_assessed": False,
            "private_data_sent": False,
        }
        assert len(calls) == 1
    finally:
        core.close()


@pytest.mark.asyncio
async def test_numes_catalogue_rejects_legacy_id_and_never_echoes_body(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        fake_http(monkeypatch, lambda _req: httpx.Response(
            404, json={"error": {"metadata": {"error_type": "not_found"},
                                 "message": MARKER}},
        ))
        result = await NumesCognitiveProvider(core.secrets).audit_endpoint_catalogue()
        assert result["http_status"] == 404
        assert result["error_type"] == "not_found"
        assert result["private_data_sent"] is False
        assert MARKER not in json.dumps(result)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_numes_503_reports_canonical_error_type_without_provider_body(
    tmp_path, monkeypatch, caplog,
):
    import logging
    configure(monkeypatch, tmp_path)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        ctx = core.cognition.compiler.compile(CognitiveRequest(
            text="Pergunta fictícia sem memórias", speaker_id="PERSON_BK",
            remote_processing_allowed=True,
        ))
        fake_http(monkeypatch, lambda _req: httpx.Response(
            503, json={"error": {
                "code": 503,
                "metadata": {"error_type": "provider_unavailable"},
                "message": MARKER,
            }},
        ))
        with caplog.at_level(logging.INFO, logger="amora.cognition.numes"):
            with pytest.raises(ProviderError) as exc:
                await NumesCognitiveProvider(core.secrets).realize(ctx)
        assert exc.value.status_code == 503
        assert exc.value.diagnostics == {
            "provider_error_category": "provider_unavailable",
        }
        assert MARKER not in str(exc.value)
        assert MARKER not in caplog.text
        assert "numes_stream_http_error" in caplog.text
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
async def test_numes_failover_uses_qwen_before_gemini(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("AMORA_SECRET_GEMINI_API_KEY", "synthetic-gemini-key")
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        req = CognitiveRequest(
            text="Estou criando um jogo novo.", speaker_id="PERSON_BK",
            remote_processing_allowed=True, session_id="synthetic",
        )
        calls = []
        async def numes(_):
            calls.append("numes")
            raise ProviderError("synthetic unavailable", status_code=503)
        async def groq(context):
            calls.append("groq")
            return {"moves": [{"kind": context.dialogue_plan["response_moves"][0],
                               "text": "Que legal, um jogo novo!"}], "closing": "statement"}
        async def gemini(context):
            calls.append("gemini")
            return {"moves": [{"kind": context.dialogue_plan["response_moves"][0],
                               "text": "Gemini reservado"}], "closing": "statement"}
        monkeypatch.setattr(core.cognition._providers["numes"], "realize", numes)
        monkeypatch.setattr(core.cognition._providers["groq"], "realize", groq)
        monkeypatch.setattr(core.cognition._providers["gemini"], "realize", gemini)
        ranked = core.models.ranked_candidates(
            TaskClass.SOCIAL_DIALOGUE, require_structured_output=True,
        )
        assert [r.name for r in ranked[:3]] == ["numes", "groq", "gemini"]
        assert "groq_memory" not in [r.name for r in ranked]
        assert ranked[1].model == "qwen/qwen3.8-27b"
        plan = await core.cognition.respond(req)
        assert plan.provider == "groq"
        assert calls == ["numes", "groq"]
    finally:
        core.close()


@pytest.mark.asyncio
async def test_qwen_failure_uses_gemini_after_numes(tmp_path, monkeypatch):
    configure(monkeypatch, tmp_path)
    monkeypatch.setenv("AMORA_SECRET_GEMINI_API_KEY", "synthetic-gemini-key")
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        req = CognitiveRequest(
            text="Estou criando um jogo novo.", speaker_id="PERSON_BK",
            remote_processing_allowed=True, session_id="synthetic",
        )
        calls = []
        async def unavailable(name, context):
            calls.append(name)
            raise ProviderError("synthetic unavailable", status_code=503)
        async def gemini(context):
            calls.append("gemini")
            return {"moves": [{"kind": context.dialogue_plan["response_moves"][0],
                               "text": "Rota secundária funcionando!"}], "closing": "statement"}
        for name in ("numes", "groq"):
            async def fake(context, label=name):
                return await unavailable(label, context)
            monkeypatch.setattr(core.cognition._providers[name], "realize", fake)
        monkeypatch.setattr(core.cognition._providers["gemini"], "realize", gemini)
        plan = await core.cognition.respond(req)
        assert plan.provider == "gemini"
        assert calls == ["numes", "groq", "gemini"]
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



def sse_reply(obj, *, finish="stop", done=True, error=None):
    content = json.dumps(obj, ensure_ascii=False)
    chunks = [
        {"choices": [{"index": 0, "delta": {"reasoning": "PRIVATE_REASONING_MARKER"}}]},
        {"choices": [{"index": 0, "delta": {"content": content[:len(content)//2]}}]},
        {"choices": [{"index": 0, "delta": {"content": content[len(content)//2:]}}]},
        {"choices": [{"index": 0, "delta": {}, "finish_reason": finish}]},
        {"choices": [], "usage": {"prompt_tokens": 100, "completion_tokens": 20}},
    ]
    if error:
        chunks.append({"error": error, "choices": []})
    raw = "".join("data: " + json.dumps(c, ensure_ascii=False) + "\n\n" for c in chunks)
    if done:
        raw += "data: [DONE]\n\n"
    return httpx.Response(200, content=raw.encode(), headers={"content-type": "text/event-stream"})


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["missing_done", "length", "in_band_429", "json_instead_of_sse"])
async def test_numes_stream_rejects_incomplete_and_inband_errors(tmp_path, monkeypatch, mode):
    configure(monkeypatch, tmp_path)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        def handler(req):
            if mode == "json_instead_of_sse":
                return httpx.Response(200, json={"answer": "4"})
            return sse_reply({"answer": "4"}, done=mode != "missing_done",
                             finish="length" if mode == "length" else "stop",
                             error={"code": 429, "message": MARKER} if mode == "in_band_429" else None)
        fake_http(monkeypatch, handler)
        with pytest.raises(ProviderError) as raised:
            await NumesCognitiveProvider(core.secrets).probe()
        if mode == "in_band_429":
            assert raised.value.status_code == 429
        assert MARKER not in str(raised.value)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_numes_probe_is_synthetic_and_does_not_write(tmp_path, monkeypatch, caplog):
    from amora.admin_commands import _command_numes_probe
    import logging
    configure(monkeypatch, tmp_path)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        def handler(req):
            payload = json.loads(req.content)
            assert payload["stream"] is True
            assert payload["provider"]["zdr"] is True
            assert payload["response_format"] == {"type": "json_object"}
            assert len(payload["messages"]) == 2
            assert payload["messages"][-1]["content"] == "What is 2 + 2?"
            return sse_reply({"answer": "4"})
        fake_http(monkeypatch, handler)
        before = core.memory.store.count()
        with caplog.at_level(logging.INFO, logger="amora.cognition.numes"):
            result = await _command_numes_probe(core, [])
        assert result["data"]["success"] is True
        assert core.memory.store.count() == before
        assert result["data"]["first_reasoning_ms"] is not None
        assert result["data"]["first_content_ms"] is not None
        records = [r for r in caplog.records if r.name == "amora.cognition.numes"]
        assert records[-1].outcome == "completed"
        assert "PRIVATE_REASONING_MARKER" not in str([r.__dict__ for r in records])
        assert "What is 2 + 2?" not in str([r.__dict__ for r in records])
    finally:
        core.close()
