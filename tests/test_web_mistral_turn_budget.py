"""Safari/Web cannot wait for a 95-second Mistral generation behind a 90s HTTP gate."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.time import SimulatedClock
from amora.models import TaskClass
from amora.web import routes as web_routes


def _bootstrap(tmp_path, monkeypatch, *, providers: bool = False):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.delenv("AMORA_SOCIAL_PRIMARY", raising=False)
    if providers:
        monkeypatch.setenv("AMORA_SECRET_NVIDIA_API_KEY", "test-nvidia-key")
        monkeypatch.setenv("AMORA_SECRET_GROQ_API_KEY", "test-groq-key")
    else:
        monkeypatch.delenv("AMORA_SECRET_NVIDIA_API_KEY", raising=False)
        monkeypatch.delenv("AMORA_SECRET_GROQ_API_KEY", raising=False)
    core = AmoraCore.bootstrap(
        clock=SimulatedClock(datetime(2026, 11, 4, 21, tzinfo=UTC))
    )
    client = TestClient(create_api(core))
    headers = {"Authorization": "Bearer " + core.secrets.get("LOCAL_API_TOKEN")}
    return core, client, headers


def test_web_budget_is_shorter_than_http_guard_and_passes_to_core(tmp_path, monkeypatch):
    assert 0 < web_routes.WEB_PROVIDER_ATTEMPT_BUDGET_SECONDS
    assert (
        web_routes.WEB_PROVIDER_ATTEMPT_BUDGET_SECONDS
        < web_routes.WEB_PROVIDER_TOTAL_BUDGET_SECONDS
        < 90.0
    )
    assert 90.0 - web_routes.WEB_PROVIDER_TOTAL_BUDGET_SECONDS >= 10.0

    core, client, headers = _bootstrap(tmp_path, monkeypatch)
    try:
        seen = []
        actual_respond = core.cognition.respond

        async def observe(request, *, on_segment=None, fallback_text=None):
            seen.append(request.metadata)
            return await actual_respond(
                request, on_segment=on_segment, fallback_text=fallback_text
            )

        core.cognition.respond = observe
        response = client.post(
            "/v1/web/utterance",
            json={"text": "Oie Amora", "remote_processing_allowed": False, "test_mode": True},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        assert response.json()["reply_queued"] is not None
        assert len(seen) == 1
        assert seen[0]["provider_attempt_budget_seconds"] == (
            web_routes.WEB_PROVIDER_ATTEMPT_BUDGET_SECONDS
        )
        assert seen[0]["provider_total_budget_seconds"] == (
            web_routes.WEB_PROVIDER_TOTAL_BUDGET_SECONDS
        )
        assert seen[0]["speech_device_id"] == "WEB_SAFARI"
        assert seen[0]["test_mode"] is True
    finally:
        client.close()
        core.close()


def test_mistral_deadline_falls_back_to_groq_before_web_http_timeout(tmp_path, monkeypatch):
    core, client, headers = _bootstrap(tmp_path, monkeypatch, providers=True)
    try:
        monkeypatch.setattr(web_routes, "WEB_PROVIDER_ATTEMPT_BUDGET_SECONDS", 0.15)
        monkeypatch.setattr(web_routes, "WEB_PROVIDER_TOTAL_BUDGET_SECONDS", 1.2)

        class DelayedMistral:
            def __init__(self):
                self.calls = 0
                self.cancelled = 0

            async def realize(self, context):
                self.calls += 1
                try:
                    await asyncio.sleep(1.0)
                except asyncio.CancelledError:
                    self.cancelled += 1
                    raise
                raise AssertionError("Mistral should have been timed out")

        class FastGroq:
            def __init__(self):
                self.calls = 0

            async def realize(self, context):
                self.calls += 1
                kind = context.dialogue_plan["response_moves"][0]
                return {
                    "moves": [{"kind": kind, "text": "Sumir por semanas e voltar sem explicar é bem estranho."}],
                    "closing": "statement",
                    "_usage": {"input_tokens": 25, "output_tokens": 12, "status_code": 200},
                }

        slow = DelayedMistral()
        fast = FastGroq()
        assert core.cognition.router.ranked_candidates(
            TaskClass.SOCIAL_DIALOGUE,
            require_structured_output=True,
        )[0].name == "nvidia_mistral_large_4"
        core.cognition._providers["nvidia_mistral_large_4"] = slow
        core.cognition._providers["groq"] = fast

        response = client.post(
            "/v1/web/utterance",
            json={"text": "O que tu acha de quem some por semanas e volta do nada?", "remote_processing_allowed": True, "test_mode": True},
            headers=headers,
        )
        assert response.status_code == 200, response.text
        result = response.json()
        assert result["reply_queued"] is not None
        assert slow.calls == 1
        assert slow.cancelled == 1
        assert fast.calls == 1
        assert result["timing"]["total_ms"] < 2000
        assert "Sumir por semanas" in result["reply_queued"]["text"]
    finally:
        client.close()
        core.close()
