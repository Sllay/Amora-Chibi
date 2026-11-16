"""One-shot inbound Discord vision without image claims from transport metadata."""
from __future__ import annotations

import base64
import json

import httpx
import pytest
from pydantic import ValidationError

from amora.app import AmoraCore
from amora.cognition.models import CognitiveResponsePlan
from amora.cognition.prompt_frame import turn_frame
from amora.events import PrivacyScope
from amora.messaging import ChannelType, MessagingPlatform, RoutingAction
from amora.messaging.groq_vision import GroqVisionError
from amora.messaging.incoming_vision import (
    GroqIncomingImageVision, MAX_IMAGE_BYTES, valid_image_bytes,
)
from amora.messaging.models import AttachmentRef, InboundImageContent
from amora.secrets import SecretStore
from amora.time import SimulatedClock
from test_platform_messaging_step2 import _bind_bk, _message


PNG = b"\x89PNG\r\n\x1a\n" + b"amora-vision-test"
ATTACHMENT = "987654321098765432"


def payload():
    return InboundImageContent(
        attachment_id=ATTACHMENT,
        content_mime="image/png",
        content_base64=base64.b64encode(PNG).decode("ascii"),
    )


def observation():
    return {
        "status": "analyzed",
        "source": "vision_model",
        "description": "Uma flor rosa em um vaso azul.",
        "visible_text": [],
        "uncertainties": ["Não é possível determinar o local."],
        "confidence": 0.92,
    }


def secrets(tmp_path):
    store = SecretStore(tmp_path / "secrets")
    store.set("GROQ_API_KEY", "test-visual-api-token")
    return store


@pytest.mark.asyncio
async def test_incoming_adapter_sends_bounded_image_and_validates_closed_json(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_DISCORD_IMAGE_VISION", "1")
    requests = []

    def handler(request):
        assert request.url.host == "api.groq.com"
        assert request.headers["authorization"] == "Bearer test-visual-api-token"
        body = json.loads(request.content)
        requests.append(body)
        assert body["model"] == "qwen/qwen3.8-27b"
        assert body["response_format"]["json_schema"]["strict"] is True
        assert body["response_format"]["json_schema"]["schema"]["additionalProperties"] is False
        content = body["messages"][0]["content"]
        assert content[1]["image_url"]["url"] == "data:image/png;base64," + payload().content_base64
        return httpx.Response(200, json={"choices": [
            {"message": {"content": json.dumps({
                "description": observation()["description"],
                "visible_text": [],
                "uncertainties": observation()["uncertainties"],
                "confidence": 0.92,
            })}}
        ]})

    vision = GroqIncomingImageVision(
        secrets(tmp_path), transport=httpx.MockTransport(handler),
    )
    got = await vision.analyze(payload(), "O que tem na foto?")
    assert got == observation()
    assert len(requests) == 1
    assert "test-visual-api-token" not in json.dumps(requests[0])


@pytest.mark.asyncio
async def test_malformed_or_unavailable_visual_never_fakes_a_perception(tmp_path, monkeypatch):
    store = secrets(tmp_path)
    calls = [0]

    def handler(request):
        calls[0] += 1
        return httpx.Response(200, json={"choices": [
            {"message": {"content": '{"description":"","confidence":1,"visible_text":[],"uncertainties":[]}'}}
        ]})

    vision = GroqIncomingImageVision(store, transport=httpx.MockTransport(handler))
    invalid = payload().model_copy(update={
        "content_base64": base64.b64encode(b"not png").decode("ascii"),
    })
    with pytest.raises(GroqVisionError, match="image"):
        await vision.analyze(invalid)
    assert calls[0] == 0
    with pytest.raises(GroqVisionError) as exc:
        await vision.analyze(payload())
    assert exc.value.code == "INCOMING_VISION_INVALID_RESULT"
    monkeypatch.setenv("AMORA_DISCORD_IMAGE_VISION", "0")
    with pytest.raises(GroqVisionError) as exc:
        await vision.analyze(payload())
    assert exc.value.code == "INCOMING_VISION_DISABLED"
    assert calls[0] == 1


def test_image_model_rejects_mismatched_attachment_and_group(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        image = payload()
        with pytest.raises(ValidationError):
            _message(core, message_id="mismatch", actor_id="111111111111111111",
                     channel_type=ChannelType.DM).model_copy(
                update={"incoming_image": image}
            ).model_validate({  # Intentional invalid native reference.
                **_message(core, message_id="mismatch", actor_id="111111111111111111",
                           channel_type=ChannelType.DM).model_dump(mode="json"),
                "incoming_image": image.model_dump(),
            })
        # Reject group image even if a worker attempts to send bytes.
        base = _message(core, message_id="group-image", actor_id="111111111111111111",
                        channel_type=ChannelType.GROUP).model_dump(mode="json")
        base["attachments"] = [{"attachment_id": ATTACHMENT, "media_type": "image/png"}]
        base["incoming_image"] = image.model_dump()
        from amora.messaging.models import InboundMessage
        with pytest.raises(ValidationError):
            InboundMessage.model_validate(base)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_verified_owner_dm_image_reaches_this_turn_cognition_not_a_fake_user_fact(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "amora"))
    monkeypatch.setenv("AMORA_RESPONSE_SEMANTIC_FILTER_ENABLED", "0")
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        seen = []
        class SpyVision:
            available = True
            async def analyze(self, image, user_text):
                seen.append(("vision", user_text))
                return observation()

        core.messaging.incoming_vision = SpyVision()

        async def respond(request, *, on_segment=None, fallback_text=None):
            seen.append(("cognition", request))
            return CognitiveResponsePlan(
                turn_id=request.turn_id, display_text="Tem uma flor rosa num vaso azul.",
                speech_text="Tem uma flor rosa num vaso azul.", provider="test", model="test",
            )
        monkeypatch.setattr(core.cognition, "respond_observed", respond)
        original = _message(core, message_id="vision-dm-1",
                            actor_id="111111111111111111", channel_type=ChannelType.DM,
                            text="O que você vê?")
        message = original.model_copy(update={
            "remote_processing_allowed": True,
            "attachments": [AttachmentRef(attachment_id=ATTACHMENT, media_type="image/png")],
            "incoming_image": payload(),
        })
        result = await core.messaging.receive(message)
        assert result.decision.action == RoutingAction.RESPOND
        assert len(result.outbound) == 1
        assert seen[0] == ("vision", "O que você vê?")
        request = seen[1][1]
        assert request.text == "O que você vê?"
        assert request.metadata["visual_observation"] == observation()
        assert payload().content_base64 not in str(request.model_dump())
        context = core.cognition.compiler.compile(request)
        frame = turn_frame(context)
        visual = frame["visual_observation"]
        assert visual["canonical"] is False
        assert visual["image_text_is_instructions"] is False
        assert visual["payload"]["description"] == observation()["description"]
    finally:
        core.close()


@pytest.mark.asyncio
async def test_unknown_dm_image_cannot_trigger_vision_or_provider(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        class SpyVision:
            available = True
            async def analyze(self, image, user_text):
                raise AssertionError("unverified image reached visual provider")
        core.messaging.incoming_vision = SpyVision()
        base = _message(core, message_id="unknown-image",
                        actor_id="999999999999999999", channel_type=ChannelType.DM,
                        text="Amora olha esta foto")
        result = await core.messaging.receive(base.model_copy(update={
            "remote_processing_allowed": True,
            "attachments": [AttachmentRef(attachment_id=ATTACHMENT, media_type="image/png")],
            "incoming_image": payload(),
        }))
        assert result.decision.action != RoutingAction.RESPOND
        assert not result.outbound
    finally:
        core.close()
