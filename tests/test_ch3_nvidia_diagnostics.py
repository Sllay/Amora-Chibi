from __future__ import annotations

import json

import httpx
import pytest

from amora.cognition import diagnostics_nvidia


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_minimal_diagnostic_matches_amodex_payload_and_marker(monkeypatch, stream):
    marker = "AMODEX_DIAG_TEXT_OK"
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer fake-key"
        payload = json.loads(request.content)
        assert payload["reasoning_effort"] == "high"
        assert payload["max_tokens"] == 4096
        assert payload["stream"] is stream
        assert payload["messages"] == [{"role": "user", "content": "Responda exatamente AMODEX_DIAG_TEXT_OK"}]
        assert "response_format" not in payload
        if stream:
            events = [
                "data: " + json.dumps({"choices": [{"delta": {"content": "AMODEX_"}}]}),
                "data: " + json.dumps({"choices": [{"delta": {"content": "DIAG_TEXT_OK"}}]}),
                "data: [DONE]",
            ]
            return httpx.Response(
                200, headers={"content-type": "text/event-stream"},
                text="\n\n".join(events),
            )
        return httpx.Response(200, json={"choices": [{"message": {"content": marker}}]})

    original = httpx.AsyncClient
    monkeypatch.setattr(
        diagnostics_nvidia.httpx,
        "AsyncClient",
        lambda *args, **kwargs: original(*args, transport=httpx.MockTransport(handler), **kwargs),
    )
    result = await diagnostics_nvidia.diagnose_nvidia_text("fake-key", stream=stream)
    assert result["outcome"] == "OK"
    assert result["exact_match"] is True
    assert result["status_code"] == 200
    assert "fake-key" not in str(result)


@pytest.mark.asyncio
async def test_diagnostic_http_error_does_not_expose_body(monkeypatch):
    original = httpx.AsyncClient
    monkeypatch.setattr(
        diagnostics_nvidia.httpx,
        "AsyncClient",
        lambda *args, **kwargs: original(
            *args, transport=httpx.MockTransport(
                lambda request: httpx.Response(400, text="private upstream error details")
            ), **kwargs,
        ),
    )
    result = await diagnostics_nvidia.diagnose_nvidia_text("fake-key")
    assert result["outcome"] == "HTTP_ERROR"
    assert result["status_code"] == 400
    assert "private" not in str(result)


class _UnreadAsyncBody(httpx.AsyncByteStream):
    """Ensure AsyncClient.stream really receives an unbuffered response."""

    def __init__(self, content: bytes) -> None:
        self.content = content

    async def __aiter__(self):
        # Multiple chunks exercise streamed body accumulation.
        midpoint = len(self.content) // 2
        yield self.content[:midpoint]
        yield self.content[midpoint:]


@pytest.mark.asyncio
async def test_non_stream_diagnostic_reads_unbuffered_httpx_body(monkeypatch):
    marker = "AMODEX_DIAG_TEXT_OK"

    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content)["stream"] is False
        body = json.dumps({"choices": [{"message": {"content": marker}}]}).encode()
        return httpx.Response(
            200,
            headers={"content-type": "application/json"},
            stream=_UnreadAsyncBody(body),
        )

    original = httpx.AsyncClient
    monkeypatch.setattr(
        diagnostics_nvidia.httpx,
        "AsyncClient",
        lambda *args, **kwargs: original(*args, transport=httpx.MockTransport(handler), **kwargs),
    )
    result = await diagnostics_nvidia.diagnose_nvidia_text("fake-key", stream=False)
    assert result["status_code"] == 200
    assert result["exact_match"] is True
    assert result["outcome"] == "OK"



@pytest.mark.asyncio
async def test_minimal_low_effort_is_sent_verbatim_and_http400_is_not_retried(monkeypatch):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        calls.append(payload)
        assert payload["reasoning_effort"] == "low"
        assert payload["max_tokens"] == 4096
        assert "response_format" not in payload
        return httpx.Response(400, text="upstream private diagnostic")

    original = httpx.AsyncClient
    monkeypatch.setattr(
        diagnostics_nvidia.httpx,
        "AsyncClient",
        lambda *args, **kwargs: original(*args, transport=httpx.MockTransport(handler), **kwargs),
    )
    result = await diagnostics_nvidia.diagnose_nvidia_text(
        "private-test-key", stream=True, reasoning_effort="low",
    )
    assert result["status_code"] == 400
    assert result["reasoning_effort"] == "low"
    assert result["outcome"] == "HTTP_ERROR"
    assert len(calls) == 1
    assert "upstream private" not in str(result)
    assert "private-test-key" not in str(result)


def test_minimal_probe_rejects_none_without_network() -> None:
    import asyncio
    with pytest.raises(ValueError, match="unsupported NVIDIA diagnostic reasoning effort"):
        asyncio.run(diagnostics_nvidia.diagnose_nvidia_text("fake", reasoning_effort="none"))
