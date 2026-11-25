"""RUN 04 synthetic visual focus and safe diagnostics."""
import json
import httpx
import pytest

from amora.cognition.visual_context import visual_context_for_request
from amora.messaging.visual_focus import visual_focus
from amora.messaging.incoming_vision import GroqIncomingImageVision, visual_failure_reason
from amora.messaging.nvidia_incoming_vision import NvidiaMistralIncomingImageVision
from amora.messaging.groq_vision import GroqVisionError
from test_discord_incoming_vision_ch2 import payload, secrets


def test_visual_focus_retains_literal_with_late_question_and_unicode():
    text = "A ideia é esta. " + "á🚀 " * 2000 + " Mas não quero isso. Qual erro aparece no screenshot?"
    focus = visual_focus(text)
    assert focus.mode == "detailed"
    assert json.loads(focus.wire())["literal_data"] == text
    assert visual_focus("").mode == "quick"
    assert visual_focus("O que vê?").mode == "directed"
    assert visual_focus("x" * 12000).literal == "x" * 12000
    with pytest.raises(ValueError, match="FOCUS_LIMIT"):
        visual_focus("x" * 12001)


@pytest.mark.asyncio
@pytest.mark.parametrize("adapter,token_field,provider", [
    (GroqIncomingImageVision, "max_completion_tokens", "groq"),
    (NvidiaMistralIncomingImageVision, "max_tokens", "nvidia"),
])
async def test_late_question_and_detailed_ocr_survive_adapter_projection(tmp_path, adapter, token_field, provider):
    stored = secrets(tmp_path)
    stored.set("NVIDIA_API_KEY", "synthetic-key")
    text = "Um contexto anterior. " + "x " * 1000 + " Qual erro de código aparece no print?"
    observed = {"description": "Interface: " + "a" * 700, "visible_text": ["erro " + "b" * 140] * 10,
                "uncertainties": ["região direita ilegível"], "confidence": .8}
    requests = []
    def handler(request):
        body = json.loads(request.content)
        requests.append(body)
        prompt = body["messages"][0]["content"][0]["text"]
        assert text in prompt
        assert body[token_field] == 2400
        return httpx.Response(200, json={"choices":[{"finish_reason":"stop",
            "message":{"content":json.dumps(observed)}}]})
    vision = adapter(stored, transport=httpx.MockTransport(handler))
    result = await vision.analyze(payload(), text)
    assert len(requests) == 1
    assert result["status"] == "analyzed"
    assert result["provider"] == provider and result["observation_version"] == 2
    assert result["description"] == observed["description"]
    assert result["visible_text"] == observed["visible_text"]
    assert visual_context_for_request(result, remote=True, private=True, mode="reply") == result
    assert visual_context_for_request(result, remote=True, private=False, mode="reply") is None
    assert visual_context_for_request(result, remote=True, private=True, mode="reflection") is None
    assert payload().content_base64 not in json.dumps(result)


@pytest.mark.parametrize("code,reason", [
    ("INCOMING_VISION_HTTP_429", "quota"),
    ("INCOMING_VISION_TIMEOUT", "timeout"),
    ("NVIDIA_VISION_REFUSAL", "refused"),
    ("INCOMING_VISION_SCHEMA_INVALID", "invalid_observation"),
    ("INCOMING_VISION_CONTENT_REJECTED", "invalid_image"),
    ("INCOMING_VISION_TRANSPORT_FAILED", "provider_unavailable"),
])
def test_visual_failure_reason_is_fixed_and_does_not_echo_provider_text(code, reason):
    exc = GroqVisionError(code, "PRIVATE_URL_OR_IMAGE_DATA")
    assert visual_failure_reason(exc) == reason
