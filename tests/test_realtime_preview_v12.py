from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from amora.cognition.models import CognitiveContext
from amora.cognition.preview import bind_preview_sink
from amora.cognition.providers_realtime import GroqCognitiveProvider, _realization_preview_text
from amora.events import PrivacyScope
from amora.latency_trace import bind_latency_trace


class _Secrets:
    def get(self, name: str):
        return "test-key" if name == "GROQ_API_KEY" else None


def _context() -> CognitiveContext:
    return CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime.now(UTC),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text="Como você tá?",
        dialogue_plan={
            "act": "answer_question",
            "depth": "medium",
            "response_moves": ["answer_question"],
            "ask_followup": False,
            "offer_help": False,
            "closing_mode": "statement",
        },
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
    )


def test_partial_json_preview_decodes_only_text_values() -> None:
    partial = '{"moves":[{"kind":"answer_question","text":"Tô bem, papis.\\nE hoje eu tô'
    assert _realization_preview_text(partial) == "Tô bem, papis.\nE hoje eu tô"
    complete = partial + ' mais animada."}],"closing":"statement"}'
    assert _realization_preview_text(complete) == "Tô bem, papis.\nE hoje eu tô mais animada."


@pytest.mark.asyncio
async def test_realtime_provider_streams_preview_but_returns_complete_json(monkeypatch) -> None:
    provider = GroqCognitiveProvider(_Secrets())
    chunks = [
        '{"moves":[{"kind":"answer_question","text":"Tô bem,',
        ' papis. Só meio elétrica hoje."}],',
        '"closing":"statement"}',
    ]

    class _Response:
        status_code = 200
        headers = {
            "x-ratelimit-remaining-requests": "99",
            "x-ratelimit-remaining-tokens": "9999",
        }

        async def aread(self):
            return b""

        async def aiter_lines(self):
            for piece in chunks:
                envelope = {"choices": [{"delta": {"content": piece}, "finish_reason": None}]}
                yield "data: " + json.dumps(envelope)
            yield "data: " + json.dumps({"choices": [{"delta": {}, "finish_reason": "stop"}]})
            yield "data: [DONE]"

    class _StreamContext:
        async def __aenter__(self):
            return _Response()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class _Client:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, *, headers, json):
            assert method == "POST"
            assert json["stream"] is True
            assert json["response_format"] == {"type": "json_object"}
            return _StreamContext()

    monkeypatch.setattr("amora.cognition.providers_realtime.httpx.AsyncClient", _Client)
    previews: list[str] = []

    async def capture(text: str) -> None:
        previews.append(text)

    with bind_latency_trace() as trace:
        with bind_preview_sink(capture):
            result = await provider.realize(_context())

    assert result["moves"] == [
        {"kind": "answer_question", "text": "Tô bem, papis. Só meio elétrica hoje."}
    ]
    assert result["closing"] == "statement"
    assert result["_usage"]["preview_streamed"] is True
    assert previews
    assert previews[-1] == "Tô bem, papis. Só meio elétrica hoje."
    assert trace["provider_model"] == provider.model
    assert trace["specialist_ms"] == 0.0
    for key in (
        "provider_headers_ms",
        "provider_first_content_ms",
        "provider_first_preview_ms",
        "provider_surface_ms",
        "provider_parse_ms",
        "provider_total_ms",
    ):
        assert key in trace
        assert float(trace[key]) >= 0.0
