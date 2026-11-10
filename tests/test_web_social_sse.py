"""Web social SSE: live keepalive/progress, safe validated final, no duplicate fallback."""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.cognition.progress import bind_social_progress, report_social_progress, unbind_social_progress
from amora.web import routes as web_routes
from amora.security import http_processing_deadline_seconds


def _client(tmp_path, monkeypatch, *, remote=False):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_SOCIAL_PRIMARY", "mistral")
    if remote:
        monkeypatch.setenv("AMORA_SECRET_NVIDIA_API_KEY", "fake-nvidia-token")
        monkeypatch.setenv("AMORA_SECRET_GROQ_API_KEY", "fake-groq-token")
    else:
        monkeypatch.delenv("AMORA_SECRET_NVIDIA_API_KEY", raising=False)
        monkeypatch.delenv("AMORA_SECRET_GROQ_API_KEY", raising=False)
    core = AmoraCore.bootstrap()
    client = TestClient(create_api(core))
    headers = {"Authorization": "Bearer " + core.secrets.get("LOCAL_API_TOKEN")}
    return core, client, headers


def _events(body: str) -> list[tuple[str, object]]:
    pairs: list[tuple[str, object]] = []
    for block in body.replace("\r\n", "\n").split("\n\n"):
        event = next((line[6:].strip() for line in block.splitlines()
                      if line.startswith("event:")), None)
        data = next((line[5:].strip() for line in block.splitlines()
                     if line.startswith("data:")), None)
        if event and data:
            pairs.append((event, json.loads(data)))
    return pairs


def test_only_authenticated_web_sse_route_gets_larger_stream_safety_ceiling() -> None:
    assert http_processing_deadline_seconds('/v1/web/utterance/stream', 'POST', worker=False) == 270
    assert http_processing_deadline_seconds('/v1/web/utterance', 'POST', worker=False) == 90
    assert http_processing_deadline_seconds('/v1/web/utterance/stream', 'GET', worker=False) == 90
    assert http_processing_deadline_seconds('/v1/web/utterance/stream', 'POST', worker=True) == 90
    assert http_processing_deadline_seconds('/v1/cognition/respond', 'POST', worker=False) == 90


def test_ephemeral_progress_channel_has_fixed_categories_and_resets() -> None:
    seen = []
    report_social_progress("reasoning")  # no subscriber
    token = bind_social_progress(seen.append)
    try:
        report_social_progress("reasoning")
        report_social_progress("writing")
        report_social_progress("PRIVATE_REASONING")
    finally:
        unbind_social_progress(token)
    report_social_progress("reasoning")
    assert seen == ["reasoning", "writing"]


def test_social_sse_requires_auth_and_reports_validated_local_turn(tmp_path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        data = {"text": "Amora, oi.", "remote_processing_allowed": False, "test_mode": True}
        assert client.post("/v1/web/utterance/stream", json=data).status_code == 401
        response = client.post("/v1/web/utterance/stream", json=data, headers=headers)
        assert response.status_code == 200
        assert "text/event-stream" in response.headers["content-type"]
        assert response.headers["cache-control"] == "no-store"
        assert response.headers["x-accel-buffering"] == "no"
        events = _events(response.text)
        assert events[0] == ("accepted", {"stage": "started"})
        assert events[-1][0] == "completed"
        assert events[-1][1]["reply_queued"]["device_id"] == "WEB_SAFARI"
        assert events[-1][1]["test_mode"] is True
        assert not any(key in str(events) for key in ["fake-nvidia-token", "fake-groq-token"])
    finally:
        client.close()
        core.close()


def test_sse_tracks_real_provider_stages_and_only_final_validated_text(tmp_path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch, remote=True)
    try:
        seen = []

        class FakeNvidia:
            async def realize(self, context):
                seen.append(context.dialogue_plan)
                report_social_progress("reasoning")
                await asyncio.sleep(0.015)
                report_social_progress("writing")
                await asyncio.sleep(0.015)
                return {
                    "moves": [{
                        "kind": context.dialogue_plan["response_moves"][0],
                        "text": "Sumir assim e voltar do nada é bem esquisito, pai.",
                    }],
                    "closing": "statement",
                    "_usage": {"input_tokens": 20, "output_tokens": 14, "status_code": 200},
                }

        core.cognition._providers["nvidia_mistral_large_4"] = FakeNvidia()
        data = {
            "text": "O que tu acha de gente que some e volta do nada?",
            "remote_processing_allowed": True,
            "test_mode": True,
        }
        response = client.post("/v1/web/utterance/stream", json=data, headers=headers)
        assert response.status_code == 200, response.text
        events = _events(response.text)
        assert seen, "Mistral social must actually be selected"
        stages = [value["stage"] for name, value in events if name == "progress"]
        assert "reasoning" in stages and "writing" in stages
        for name, value in events:
            if name != "completed":
                assert "Sumir assim" not in str(value)
                assert "reasoning_content" not in str(value)
        final = events[-1]
        assert final[0] == "completed"
        assert "Sumir assim" in final[1]["reply_queued"]["text"]
        assert final[1]["timing"]["total_ms"] < 2000
        assert final[1]["reply_queued"]["device_id"] == "WEB_SAFARI"
        assert web_routes.WEB_STREAM_PROVIDER_ATTEMPT_BUDGET_SECONDS > 95
        assert web_routes.WEB_STREAM_PROVIDER_TOTAL_BUDGET_SECONDS > web_routes.WEB_STREAM_PROVIDER_ATTEMPT_BUDGET_SECONDS
    finally:
        client.close()
        core.close()


def test_sse_failure_returns_safe_code_never_exception_or_model_text(tmp_path, monkeypatch) -> None:
    core, client, headers = _client(tmp_path, monkeypatch)
    try:
        # Exercise the same public endpoint while making the Core pipeline
        # throw inside the generator; the SSE has already started with HTTP200.
        monkeypatch.setattr(core.language_social, "observe_text",
                            lambda *args, **kwargs: _raise_private())
        response = client.post(
            "/v1/web/utterance/stream",
            json={"text": "Oi", "test_mode": True},
            headers=headers,
        )
        assert response.status_code == 200
        events = _events(response.text)
        assert events[0][0] == "accepted"
        assert events[-1] == ("failed", {"error_code": "WEB_TURN_PROCESSING_FAILED"})
        assert "PRIVATE_SPEECH_AND_API_KEY_SHOULD_NOT_LEAK" not in response.text
    finally:
        client.close()
        core.close()


def _raise_private():
    raise ValueError("PRIVATE_SPEECH_AND_API_KEY_SHOULD_NOT_LEAK")
