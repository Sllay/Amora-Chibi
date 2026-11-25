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
    assert requests[0]["reasoning_effort"] == "none"
    assert requests[0]["max_completion_tokens"] == 1400
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
    assert exc.value.code == "INCOMING_VISION_SCHEMA_INVALID"
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


@pytest.mark.asyncio
@pytest.mark.parametrize("envelope,expected", [
    ({"choices":[{"finish_reason":"length","message":{"content":""}}]}, "INCOMING_VISION_TRUNCATED"),
    ({"choices":[{"finish_reason":"stop","message":{"content":None}}]}, "INCOMING_VISION_EMPTY_RESPONSE"),
    ({"choices":[{"finish_reason":"stop","message":{"content":"not-json"}}]}, "INCOMING_VISION_JSON_INVALID"),
    ({"choices":[{"finish_reason":"tool_calls","message":{"content":"{}"}}]}, "INCOMING_VISION_FINISH_UNEXPECTED"),
    ({"choices":[]}, "INCOMING_VISION_ENVELOPE_INVALID"),
])
async def test_provider_failure_codes_do_not_echo_private_response(tmp_path, envelope, expected):
    private = "secret-face-name-must-never-leak"
    def handler(_request):
        return httpx.Response(200, json=envelope)
    vision = GroqIncomingImageVision(secrets(tmp_path), transport=httpx.MockTransport(handler))
    with pytest.raises(GroqVisionError) as error:
        await vision.analyze(payload(), "olha " + private)
    assert error.value.code == expected
    assert private not in str(error.value)
    assert private not in error.value.code


WEBP = b"RIFF" + (8).to_bytes(4, "little") + b"WEBP" + b"VP8 "


def test_core_accepts_bound_transcoded_webp_for_native_png_only(tmp_path, monkeypatch):
    from amora.messaging.models import InboundMessage
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        base = _message(
            core, message_id="large-art-webp", actor_id="111111111111111111",
            channel_type=ChannelType.DM, text="Olha a arte",
        ).model_dump(mode="json")
        base["attachments"] = [
            {"attachment_id": ATTACHMENT, "media_type": "image/png", "size_bytes": 9_000_000}
        ]
        base["incoming_image"] = {
            "attachment_id": ATTACHMENT,
            "content_mime": "image/webp",
            "content_base64": base64.b64encode(WEBP).decode("ascii"),
        }
        message = InboundMessage.model_validate(base)
        assert message.incoming_image.content_mime == "image/webp"
        assert valid_image_bytes(message.incoming_image) == WEBP

        # Do not let nonimage attachment metadata or mismatched attachment
        # identities authorize bytes with a valid raster header.
        base["attachments"][0]["media_type"] = "application/zip"
        with pytest.raises(ValidationError):
            InboundMessage.model_validate(base)
        base["attachments"][0]["media_type"] = "image/png"
        base["incoming_image"]["attachment_id"] = "888888888888888888"
        with pytest.raises(ValidationError):
            InboundMessage.model_validate(base)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_owner_image_only_failure_is_conveyed_without_fake_observation(tmp_path, monkeypatch):
    from amora.cognition.visual_context import visual_context_for_request

    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        seen = []

        class PresentVision:
            available = True

            async def analyze(self, image, user_text):
                raise AssertionError("image bytes were not fetched")

        core.messaging.incoming_vision = PresentVision()

        async def respond(request, *, on_segment=None, fallback_text=None):
            seen.append(request)
            return CognitiveResponsePlan(
                turn_id=request.turn_id, display_text="Não consegui abrir essa imagem.",
                speech_text="Não consegui abrir essa imagem.", provider="test", model="test",
            )

        monkeypatch.setattr(core.cognition, "respond_observed", respond)
        original = _message(
            core, message_id="large-art-overlimit", actor_id="111111111111111111",
            channel_type=ChannelType.DM, text="placeholder",
        )
        message = original.model_copy(update={
            "text": "",
            "remote_processing_allowed": True,
            "attachments": [
                AttachmentRef(attachment_id=ATTACHMENT, media_type="image/png",
                              size_bytes=30 * 1024 * 1024),
            ],
            "incoming_image": None,
            "incoming_image_status": "IMAGE_NATIVE_TOO_LARGE",
        })
        response = await core.messaging.receive(message)
        assert response.decision.action == RoutingAction.RESPOND
        assert seen
        request = seen[0]
        assert "não conseguiu" in request.text
        assert request.metadata["visual_observation"]["status"] == "unavailable"
        assert request.metadata["visual_observation"]["failure_reason"] == "too_large"
        frame = turn_frame(core.cognition.compiler.compile(request))
        assert frame["visual_observation"]["canonical"] is False
        assert frame["visual_observation"]["payload"]["failure_reason"] == "too_large"
        assert visual_context_for_request(
            request.metadata["visual_observation"], remote=True, private=True, mode="social"
        ) == request.metadata["visual_observation"]
    finally:
        core.close()


def test_native_heic_and_avif_are_only_accepted_after_provider_safe_webp_conversion(
    tmp_path, monkeypatch,
):
    from amora.messaging.models import InboundMessage
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        for media_type in ("image/heic", "image/heif", "image/avif"):
            base = _message(
                core, message_id="format-" + media_type, actor_id="111111111111111111",
                channel_type=ChannelType.DM, text="Olha a imagem",
            ).model_dump(mode="json")
            base["attachments"] = [
                {"attachment_id": ATTACHMENT, "media_type": media_type}
            ]
            base["incoming_image"] = {
                "attachment_id": ATTACHMENT,
                "content_mime": "image/webp",
                "content_base64": base64.b64encode(WEBP).decode("ascii"),
            }
            assert InboundMessage.model_validate(base).incoming_image.content_mime == "image/webp"
            base["incoming_image"]["content_mime"] = media_type
            with pytest.raises(ValidationError):
                InboundMessage.model_validate(base)
    finally:
        core.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_code,media_type,expected_reason", [
    ("IMAGE_TRANSPORT_TOO_LARGE", "image/png", "too_large"),
    ("IMAGE_UNSUPPORTED_FORMAT", "image/gif", "unsupported_format"),
])
async def test_owner_discord_image_not_silenced_after_ingress_failure(
    tmp_path, monkeypatch, failure_code, media_type, expected_reason,
):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        seen = []

        class AvailableVision:
            available = True

            async def analyze(self, image, user_text):
                raise AssertionError("No image bytes should be sent")

        core.messaging.incoming_vision = AvailableVision()

        async def respond(request, *, on_segment=None, fallback_text=None):
            seen.append(request)
            return CognitiveResponsePlan(
                turn_id=request.turn_id,
                display_text="Não consegui abrir essa imagem.",
                speech_text="Não consegui abrir essa imagem.",
                provider="test", model="test",
            )

        monkeypatch.setattr(core.cognition, "respond_observed", respond)
        original = _message(
            core, message_id="failed-image-" + failure_code,
            actor_id="111111111111111111", channel_type=ChannelType.DM,
            text="test-placeholder",
        )
        image_only = original.model_copy(update={
            "text": "", "remote_processing_allowed": True,
            "attachments": [AttachmentRef(
                attachment_id=ATTACHMENT, media_type=media_type,
            )],
            "incoming_image_status": failure_code,
        })
        result = await core.messaging.receive(image_only)
        assert result.decision.action == RoutingAction.RESPOND
        assert len(result.outbound) == 1
        assert len(seen) == 1
        request = seen[0]
        assert "não conseguiu" in request.text
        assert request.metadata["visual_observation"]["status"] == "unavailable"
        assert request.metadata["visual_observation"]["failure_reason"] == expected_reason
    finally:
        core.close()


def test_inline_fallback_size_boundary_is_below_4_mb_encoded():
    from amora.messaging.incoming_vision import MAX_BASE64_LENGTH
    assert MAX_IMAGE_BYTES == 2_800_000
    assert MAX_BASE64_LENGTH == 3_733_336
    valid = bytes([137, 80, 78, 71, 13, 10, 26, 10])
    raw = valid + bytes(MAX_IMAGE_BYTES - len(valid))
    image = InboundImageContent(
        attachment_id=ATTACHMENT, content_mime="image/png",
        content_base64=base64.b64encode(raw).decode("ascii"),
    )
    assert len(valid_image_bytes(image)) == MAX_IMAGE_BYTES
    with pytest.raises(ValidationError):
        InboundImageContent(
            attachment_id=ATTACHMENT, content_mime="image/png",
            content_base64=image.content_base64 + "AAAA",
        )


@pytest.mark.asyncio
async def test_qwen_prefers_20mb_signed_discord_url_and_falls_back_on_404(tmp_path):
    source = ("https://cdn.discordapp.com/attachments/"
              "123456789012345678/" + ATTACHMENT + "/art.png"
              "?ex=aaaa&is=bbbb&hm=cccc")
    image = payload().model_copy(update={"source_url": source})
    calls = []

    def handler(request):
        body = json.loads(request.content)
        seen = body["messages"][0]["content"][1]["image_url"]["url"]
        calls.append(seen)
        if len(calls) == 1:
            assert seen == source
            return httpx.Response(404, json={"error": "signed original not available"})
        assert seen == "data:image/png;base64," + image.content_base64
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop",
            "message": {"content": json.dumps({
                "description": observation()["description"],
                "visible_text": [], "uncertainties": observation()["uncertainties"],
                "confidence": observation()["confidence"],
            })}
        }]})

    vision = GroqIncomingImageVision(secrets(tmp_path), transport=httpx.MockTransport(handler))
    assert await vision.analyze(image) == observation()
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_qwen_original_discord_url_success_never_sends_thumbnail(tmp_path):
    source = ("https://cdn.discordapp.com/attachments/"
              "123456789012345678/" + ATTACHMENT + "/art.png?ex=aaaa")
    image = payload().model_copy(update={"source_url": source})
    calls = []

    def handler(request):
        body = json.loads(request.content)
        calls.append(body["messages"][0]["content"][1]["image_url"]["url"])
        return httpx.Response(200, json={"choices": [{
            "finish_reason": "stop", "message": {"content": json.dumps({
                "description": observation()["description"],
                "visible_text": [], "uncertainties": observation()["uncertainties"],
                "confidence": observation()["confidence"],
            })}
        }]})

    vision = GroqIncomingImageVision(secrets(tmp_path), transport=httpx.MockTransport(handler))
    assert await vision.analyze(image) == observation()
    assert calls == [source]


@pytest.mark.parametrize("bad_url", [
    "https://evil.example/attachments/123456789012345678/" + ATTACHMENT + "/a.png",
    "http://cdn.discordapp.com/attachments/123456789012345678/" + ATTACHMENT + "/a.png",
    "https://cdn.discordapp.com/attachments/888888888888888888/" + ATTACHMENT + "/a.png",
    "https://cdn.discordapp.com/attachments/123456789012345678/888888888888888888/a.png",
    "https://cdn.discordapp.com/attachments/123456789012345678/" + ATTACHMENT + "/a.png?redirect=https://evil.example",
    "https://cdn.discordapp.com.evil.example/attachments/123456789012345678/" + ATTACHMENT + "/a.png",
])
def test_core_rejects_untrusted_discord_cdn_image_urls(tmp_path, monkeypatch, bad_url):
    from amora.messaging.models import InboundMessage

    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        base = _message(
            core, message_id="url-boundary", actor_id="111111111111111111",
            channel_type=ChannelType.DM, text="olha",
        ).model_dump(mode="json")
        base["conversation_id"] = "123456789012345678"
        base["remote_processing_allowed"] = True
        base["attachments"] = [
            {"attachment_id": ATTACHMENT, "media_type": "image/png"}
        ]
        base["incoming_image"] = {
            **payload().model_dump(mode="json"), "source_url": bad_url,
        }
        with pytest.raises(ValidationError):
            InboundMessage.model_validate(base)
        base["incoming_image"]["source_url"] = (
            "https://cdn.discordapp.com/attachments/123456789012345678/"
            + ATTACHMENT + "/a.png?ex=aaaa&is=bbbb&hm=cccc"
        )
        assert InboundMessage.model_validate(base).incoming_image.source_url.startswith(
            "https://cdn.discordapp.com/"
        )
        base["remote_processing_allowed"] = False
        with pytest.raises(ValidationError):
            InboundMessage.model_validate(base)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_visual_url_retry_is_denied_if_authorization_is_lost(tmp_path):
    from amora.messaging.visual_authorization import VISUAL_AUTHORIZATION
    from amora.messaging.incoming_vision import IncomingVisionCascade
    requests = []
    async def guard():
        return not requests
    def handler(request):
        requests.append(request)
        return httpx.Response(404, json={"error":"synthetic unavailable"})
    image = payload().model_copy(update={
        "source_url": f"https://cdn.discordapp.com/attachments/123456789012345678/{ATTACHMENT}/foto.png?ex=abc"
    })
    provider = GroqIncomingImageVision(secrets(tmp_path),transport=httpx.MockTransport(handler))
    token = VISUAL_AUTHORIZATION.set(guard)
    try:
        with pytest.raises(GroqVisionError) as caught:
            await IncomingVisionCascade(provider).analyze(image,"Descreva a imagem.")
        assert caught.value.code == "INCOMING_VISION_AUTHORIZATION_LOST"
        assert len(requests) == 1
    finally:
        VISUAL_AUTHORIZATION.reset(token)


@pytest.mark.asyncio
async def test_visual_cascade_denies_second_attempt_and_fallback_after_revocation(monkeypatch):
    from amora.messaging.visual_authorization import VISUAL_AUTHORIZATION
    from amora.messaging.incoming_vision import IncomingVisionCascade
    for fallback_enabled in ("false","true"):
        monkeypatch.setenv("AMORA_VISION_NVIDIA_FALLBACK",fallback_enabled)
        calls = []
        class Primary:
            available = True
            async def analyze(self,image,user_text):
                calls.append("primary")
                raise GroqVisionError("INCOMING_VISION_TRANSPORT_FAILED","synthetic")
        class Fallback:
            available = True
            async def analyze(self,image,user_text):
                calls.append("fallback")
                return observation()
        async def guard():
            return not calls
        token = VISUAL_AUTHORIZATION.set(guard)
        try:
            with pytest.raises(GroqVisionError) as caught:
                await IncomingVisionCascade(Primary(),Fallback()).analyze(payload(),"Veja a foto.")
            assert caught.value.code == "INCOMING_VISION_AUTHORIZATION_LOST"
            assert calls == ["primary"]
        finally:
            VISUAL_AUTHORIZATION.reset(token)
