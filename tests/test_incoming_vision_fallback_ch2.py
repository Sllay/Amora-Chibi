"""Vision provider routing is distinct from social fallback and must be opt-in."""
from __future__ import annotations

import json

import httpx
import pytest

from amora.messaging.groq_vision import GroqVisionError
from amora.messaging.incoming_vision import (
    GroqIncomingImageVision, IncomingVisionCascade,
)
from amora.messaging.nvidia_incoming_vision import NvidiaMistralIncomingImageVision
from test_discord_incoming_vision_ch2 import payload, secrets, observation


class MockVisual:
    def __init__(self, *, available=True, answer=None, error=None):
        self.available = available
        self.answer = answer or observation()
        self.error = error
        self.calls = 0

    async def analyze(self, image, user_text):
        self.calls += 1
        if self.error:
            raise GroqVisionError(self.error, "safe simulated failure")
        return self.answer


@pytest.mark.asyncio
async def test_no_second_provider_without_opt_in(monkeypatch):
    monkeypatch.delenv("AMORA_VISION_NVIDIA_FALLBACK", raising=False)
    primary = MockVisual(error="INCOMING_VISION_SCHEMA_INVALID")
    fallback = MockVisual()
    chain = IncomingVisionCascade(primary, fallback)
    with pytest.raises(GroqVisionError) as exc:
        await chain.analyze(payload(), "olha")
    assert exc.value.code == "INCOMING_VISION_SCHEMA_INVALID"
    assert primary.calls == 1 and fallback.calls == 0


@pytest.mark.asyncio
async def test_one_fallback_after_qwen_invalid_output(monkeypatch):
    monkeypatch.setenv("AMORA_VISION_NVIDIA_FALLBACK", "true")
    primary = MockVisual(error="INCOMING_VISION_TRUNCATED")
    fallback = MockVisual()
    chain = IncomingVisionCascade(primary, fallback)
    assert await chain.analyze(payload(), "olha") == observation()
    assert primary.calls == 1 and fallback.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("primary_error", [
    "INCOMING_VISION_HTTP_401",
    "INCOMING_VISION_HTTP_402",
    "INCOMING_VISION_HTTP_400",
    "INCOMING_VISION_DISABLED",
    "INCOMING_VISION_CONTENT_REJECTED",
])
async def test_deterministic_or_protected_failure_does_not_trigger_fallback(monkeypatch, primary_error):
    monkeypatch.setenv("AMORA_VISION_NVIDIA_FALLBACK", "1")
    primary = MockVisual(error=primary_error)
    fallback = MockVisual()
    chain = IncomingVisionCascade(primary, fallback)
    with pytest.raises(GroqVisionError) as exc:
        await chain.analyze(payload())
    assert exc.value.code == primary_error
    assert fallback.calls == 0


@pytest.mark.asyncio
async def test_no_key_and_no_enabled_provider_means_unavailable(monkeypatch):
    monkeypatch.setenv("AMORA_VISION_NVIDIA_FALLBACK", "true")
    primary = MockVisual(available=False)
    fallback = MockVisual(available=False)
    chain = IncomingVisionCascade(primary, fallback)
    assert chain.available is False
    monkeypatch.setenv("AMORA_VISION_NVIDIA_FALLBACK", "false")
    fallback.available = True
    assert chain.available is False


@pytest.mark.asyncio
async def test_fallback_can_serve_if_primary_key_missing_but_opted_in(monkeypatch):
    monkeypatch.setenv("AMORA_VISION_NVIDIA_FALLBACK", "yes")
    primary = MockVisual(available=False)
    fallback = MockVisual()
    chain = IncomingVisionCascade(primary, fallback)
    assert chain.available is True
    assert await chain.analyze(payload()) == observation()
    assert primary.calls == 0 and fallback.calls == 1


@pytest.mark.asyncio
async def test_secondary_failure_never_uses_fabricated_visual_claim(monkeypatch):
    monkeypatch.setenv("AMORA_VISION_NVIDIA_FALLBACK", "on")
    primary = MockVisual(error="INCOMING_VISION_EMPTY_RESPONSE")
    fallback = MockVisual(error="NVIDIA_VISION_SCHEMA_INVALID")
    chain = IncomingVisionCascade(primary, fallback)
    with pytest.raises(GroqVisionError) as exc:
        await chain.analyze(payload(), "minha foto")
    assert exc.value.code == "NVIDIA_VISION_SCHEMA_INVALID"
    assert primary.calls == fallback.calls == 1


@pytest.mark.asyncio
async def test_nvidia_contract_receives_only_allowed_image_and_uses_local_schema(tmp_path):
    stored = secrets(tmp_path)
    stored.set("NVIDIA_API_KEY", "nvidia-test-vision-token")
    captured = []

    def handler(request):
        assert request.url.host == "integrate.api.nvidia.com"
        assert request.headers["authorization"] == "Bearer nvidia-test-vision-token"
        envelope = json.loads(request.content)
        captured.append(envelope)
        assert envelope["model"] == "mistralai/mistral-large-4-1t-a52b"
        assert envelope["reasoning_effort"] == "none"
        assert envelope["stream"] is False
        assert "response_format" not in envelope  # Hosted strict-schema support not yet certified
        items = envelope["messages"][0]["content"]
        assert items[1]["image_url"]["url"].startswith("data:image/png;base64,")
        assert envelope["max_tokens"] == 1400
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {
                "content": json.dumps({
                    "description": observation()["description"],
                    "visible_text": [],
                    "uncertainties": observation()["uncertainties"],
                    "confidence": observation()["confidence"],
                })
            }}]
        })

    provider = NvidiaMistralIncomingImageVision(
        stored, transport=httpx.MockTransport(handler)
    )
    assert provider.available
    assert await provider.analyze(payload(), "olha a foto") == observation()
    assert len(captured) == 1
    assert "nvidia-test-vision-token" not in json.dumps(captured[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("response,code", [
    ({"choices": [{"finish_reason": "length", "message": {"content": None}}]}, "NVIDIA_VISION_TRUNCATED"),
    ({"choices": [{"finish_reason": "stop", "message": {"content": "```json\n{}\n```"}}]}, "NVIDIA_VISION_JSON_INVALID"),
    ({"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}, "NVIDIA_VISION_SCHEMA_INVALID"),
    ({"choices": []}, "NVIDIA_VISION_ENVELOPE_INVALID"),
])
async def test_nvidia_does_not_accept_invalid_json_or_partial_results(tmp_path, response, code):
    stored = secrets(tmp_path)
    stored.set("NVIDIA_API_KEY", "nvidia-test-vision-token")
    provider = NvidiaMistralIncomingImageVision(
        stored,
        transport=httpx.MockTransport(lambda req: httpx.Response(200, json=response)),
    )
    with pytest.raises(GroqVisionError) as exc:
        await provider.analyze(payload(), "nome privado não deve vazar")
    assert exc.value.code == code
    assert "nome privado" not in str(exc.value)


@pytest.mark.asyncio
async def test_bounded_cascade_cannot_wait_unbounded_on_stalled_primary(monkeypatch):
    import asyncio

    monkeypatch.setenv("AMORA_VISION_NVIDIA_FALLBACK", "true")
    class SlowPrimary(MockVisual):
        async def analyze(self, image, user_text):
            self.calls += 1
            await asyncio.sleep(1)
            return observation()
    primary = SlowPrimary()
    secondary = MockVisual()
    chain = IncomingVisionCascade(
        primary, secondary, primary_timeout_seconds=0.001,
        total_timeout_seconds=0.3,
    )
    assert await chain.analyze(payload()) == observation()
    assert primary.calls == 1 and secondary.calls == 1



@pytest.mark.asyncio
async def test_same_valid_image_recovers_from_one_intermitent_empty_groq_response(monkeypatch):
    monkeypatch.delenv("AMORA_VISION_NVIDIA_FALLBACK", raising=False)
    class FlakyPrimary(MockVisual):
        async def analyze(self, image, user_text):
            self.calls += 1
            if self.calls == 1:
                raise GroqVisionError(
                    "INCOMING_VISION_EMPTY_RESPONSE", "synthetic empty first response"
                )
            return observation()
    groq = FlakyPrimary()
    fallback = MockVisual()
    cascade = IncomingVisionCascade(groq, fallback)
    assert await cascade.analyze(payload(), "O que vê nessa imagem?") == observation()
    assert groq.calls == 2
    assert fallback.calls == 0  # no second-provider privacy escalation


@pytest.mark.asyncio
async def test_rate_limited_groq_never_loops_or_spends_more_requests(monkeypatch):
    monkeypatch.delenv("AMORA_VISION_NVIDIA_FALLBACK", raising=False)
    groq = MockVisual(error="INCOMING_VISION_HTTP_429")
    cascade = IncomingVisionCascade(groq, MockVisual())
    with pytest.raises(GroqVisionError) as error:
        await cascade.analyze(payload())
    assert error.value.code == "INCOMING_VISION_HTTP_429"
    assert groq.calls == 1


@pytest.mark.asyncio
async def test_fast_transient_retry_obeys_remaining_visual_budget(monkeypatch):
    monkeypatch.delenv("AMORA_VISION_NVIDIA_FALLBACK", raising=False)
    groq = MockVisual(error="INCOMING_VISION_TRANSPORT_FAILED")
    cascade = IncomingVisionCascade(groq, MockVisual(),
                                    total_timeout_seconds=0.2)
    with pytest.raises(GroqVisionError) as error:
        await cascade.analyze(payload())
    assert error.value.code == "INCOMING_VISION_TRANSPORT_FAILED"
    assert groq.calls == 1  # less than 5 seconds left: no retry



@pytest.mark.asyncio
async def test_cdn_failure_retries_inline_verified_pixels_in_same_groq_provider(monkeypatch):
    monkeypatch.delenv("AMORA_VISION_NVIDIA_FALLBACK", raising=False)
    class FlakySource(MockVisual):
        def __init__(self):
            super().__init__()
            self.urls = []
        async def analyze(self, image, user_text):
            self.calls += 1
            self.urls.append(image.source_url)
            if self.calls == 1:
                raise GroqVisionError(
                    "INCOMING_VISION_EMPTY_RESPONSE", "provider gave no visual observation",
                )
            assert image.source_url is None
            assert image.content_base64 == initial.content_base64
            assert user_text == "Essa é a mesma imagem?"
            return observation()
    initial = payload().model_copy(update={
        "source_url": "https://cdn.discordapp.com/attachments/"
                      "123456789012345678/987654321098765432/imagem.png"
                      "?ex=abcd&is=ef01&hm=ffee",
    })
    groq = FlakySource()
    cascade = IncomingVisionCascade(groq, MockVisual())
    assert await cascade.analyze(initial, "Essa é a mesma imagem?") == observation()
    assert groq.urls == [initial.source_url, None]
    assert groq.calls == 2
