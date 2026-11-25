"""Vision reliability: mixed photo/caption, unavailable sensor, deadline budgets."""
from __future__ import annotations

import pytest

from amora.app import AmoraCore
from amora.cognition.models import CognitiveResponsePlan
from amora.cognition.realization import realization_instruction
from amora.events import PrivacyScope
from amora.messaging import ChannelType
from amora.messaging.groq_vision import GroqVisionError
from amora.messaging.models import AttachmentRef
from amora.time import SimulatedClock
from test_discord_incoming_vision_ch2 import payload
from test_platform_messaging_step2 import _bind_bk, _message
from test_phase2_context_contracts import context

ATTACHMENT = "987654321098765432"


@pytest.mark.asyncio
async def test_image_and_caption_stay_in_same_authorized_turn_even_on_visual_failure(
    tmp_path, monkeypatch,
):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        class Vision:
            available = True
            async def analyze(self, image, user_text):
                assert user_text == "Essa é a textura nova do coelho?"
                raise GroqVisionError("INCOMING_VISION_TRANSPORT_FAILED", "synthetic error")
        core.messaging.incoming_vision = Vision()
        seen = []
        async def respond(request, *, on_segment=None, fallback_text=None):
            seen.append(request)
            return CognitiveResponsePlan(
                turn_id=request.turn_id,
                display_text="Consigo ler sua pergunta, mas a imagem não abriu.",
                speech_text="Consigo ler sua pergunta, mas a imagem não abriu.",
                provider="test", model="test",
            )
        monkeypatch.setattr(core.cognition, "respond_observed", respond)
        original = _message(
            core, message_id="mixed-vision-failure",
            actor_id="111111111111111111", channel_type=ChannelType.DM,
            text="Essa é a textura nova do coelho?",
        )
        incoming = original.model_copy(update={
            "remote_processing_allowed": True,
            "attachments": [AttachmentRef(attachment_id=ATTACHMENT, media_type="image/png")],
            "incoming_image": payload(),
        })
        result = await core.messaging.receive(incoming)
        assert result.outbound
        assert seen[0].text == "Essa é a textura nova do coelho?"
        assert seen[0].metadata["visual_observation"]["status"] == "unavailable"
        assert seen[0].metadata["visual_observation"]["failure_reason"] == "provider_unavailable"
        compiled = core.cognition.compiler.compile(seen[0])
        instruction = realization_instruction(compiled)
        assert "responda normalmente ao texto" in instruction
        assert "não conseguiu visualizar" in instruction
    finally:
        core.close()


def test_multimodal_instruction_distinguishes_real_evidence_from_missing_pixels():
    observed = {
        "status": "analyzed", "source": "vision_model",
        "description": "Uma flor rosa sobre fundo branco.",
        "confidence": 0.94, "visible_text": [], "uncertainties": [],
    }
    supported = realization_instruction(context("O que você vê?", visual_observation=observed))
    assert "Responda ao pedido literal" in supported
    assert "sem ignorar a legenda" in supported

    unavailable = {
        "status": "unavailable", "source": "core_unavailable",
        "failure_reason": "provider_failed",
    }
    missing = realization_instruction(context("O que vê?", visual_observation=unavailable))
    assert "não conseguiu visualizar a imagem" in missing
    assert "nunca alegue ter visto" in missing


def test_image_http_guard_includes_unchanged_vision_and_cognition_budgets():
    from amora.security import http_processing_deadline_seconds
    from amora.messaging.service import (
        _MESSAGING_COGNITION_TIMEOUT_SECONDS,
        _MESSAGING_PROVIDER_ATTEMPT_BUDGET_SECONDS,
        _MESSAGING_PROVIDER_TOTAL_BUDGET_SECONDS,
    )
    assert (_MESSAGING_PROVIDER_ATTEMPT_BUDGET_SECONDS,
            _MESSAGING_PROVIDER_TOTAL_BUDGET_SECONDS,
            _MESSAGING_COGNITION_TIMEOUT_SECONDS) == (75, 90, 105)
    # Worker image-specific deadline 160s < Core's HTTP guard.
    assert 27 + _MESSAGING_COGNITION_TIMEOUT_SECONDS < 160
    assert http_processing_deadline_seconds(
        "/v1/messaging/worker/inbound", "POST", worker=False) == 175
    assert http_processing_deadline_seconds(
        "/v1/messaging/worker/inbound", "POST", worker=True) == 175


def _batch_message(core, *, channel=ChannelType.DM, remote=True):
    from amora.messaging.models import InboundMessage
    raw = _message(core, message_id="mixed-batch", actor_id="111111111111111111",
                   channel_type=channel, text="Compare a primeira e a terceira imagem.").model_dump()
    ids = [ATTACHMENT, "987654321098765433", "987654321098765434", "987654321098765435"]
    raw.update(remote_processing_allowed=remote,
        attachments=[{"attachment_id":i,"media_type":"image/png"} for i in ids],
        incoming_images=[
            {"attachment_id":ids[0],"image":payload().model_dump()},
            {"attachment_id":ids[1],"ingest_status":"IMAGE_FETCH_FAILED"},
            {"attachment_id":ids[2],"image":payload().model_copy(update={"attachment_id":ids[2]}).model_dump()},
        ])
    return InboundMessage.model_validate(raw)


@pytest.mark.asyncio
async def test_ordered_image_batch_partial_caption_and_no_raw_media_in_context(tmp_path, monkeypatch):
    from amora.cognition.visual_context import visual_context_for_request
    from test_discord_incoming_vision_ch2 import observation
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        msg = _batch_message(core)
        calls = []
        class Vision:
            available = True
            async def analyze(self, image, user_text):
                calls.append((image.attachment_id,user_text))
                return {**observation(),"description":f"Observação {len(calls)}"}
        core.messaging.incoming_vision = Vision()
        seen = []
        async def respond(request, *, on_segment=None, fallback_text=None):
            seen.append(request)
            return CognitiveResponsePlan(turn_id=request.turn_id, display_text="Comparei as observações disponíveis.",
                speech_text="Comparei as observações disponíveis.", provider="test", model="test")
        monkeypatch.setattr(core.cognition,"respond_observed",respond)
        result = await core.messaging.receive(msg)
        assert result.outbound and len(seen) == 1
        assert seen[0].text == msg.text
        assert calls == [(msg.attachments[0].attachment_id,msg.text),(msg.attachments[2].attachment_id,msg.text)]
        visual = seen[0].metadata["visual_observation"]
        assert visual["status"] == "partial" and visual["image_count"] == 4
        assert [x["image_number"] for x in visual["images"]] == [1,2,3]
        assert visual["images"][1]["observation"]["failure_reason"] == "download_failed"
        assert visual["images"][2]["observation"]["description"] == "Observação 2"
        assert "content_base64" not in str(visual)
        assert all(a.attachment_id not in str(visual) for a in msg.attachments)
        assert visual_context_for_request(visual, remote=True, private=True, mode="reply") == visual
        assert visual_context_for_request(visual, remote=True, private=False, mode="reply") is None
        assert visual_context_for_request(visual, remote=True, private=True, mode="reflection") is None
        assert visual_context_for_request({**visual,"status":"analyzed"},remote=True,private=True,mode="reply") is None
        assert "Compare somente as observações analisadas" in realization_instruction(context(msg.text,visual_observation=visual))
    finally:
        core.close()


def test_batch_rejects_bad_identity_order_consent_and_aggregate(tmp_path, monkeypatch):
    from pydantic import ValidationError
    from amora.messaging.models import InboundMessage
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        good = _batch_message(core)
        raw = good.model_dump()
        for changes in [
            {"remote_processing_allowed":False},
            {"channel_type":"group"},
            {"incoming_image":payload().model_dump()},
            {"incoming_images":list(reversed(raw["incoming_images"]))},
            {"incoming_images":[raw["incoming_images"][0]]*2},
        ]:
            with pytest.raises(ValidationError):
                InboundMessage.model_validate({**raw,**changes})
        oversized = good.model_dump()
        for item in oversized["incoming_images"]:
            if item["image"]:
                item["image"]["content_base64"] = "A"*2_000_000
        with pytest.raises(ValidationError,match="aggregate payload"):
            InboundMessage.model_validate(oversized)
    finally:
        core.close()


@pytest.mark.asyncio
async def test_visual_batch_retry_reuses_only_successful_images(tmp_path,monkeypatch):
    from amora.messaging.models import AttentionDecision, RoutingAction
    from test_discord_incoming_vision_ch2 import observation
    from amora.messaging.visual_checkpoint import VisualCheckpointStore
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        msg = _batch_message(core)
        decision = AttentionDecision(action=RoutingAction.RESPOND, score=1,
            speaker_entity_id="PERSON_BK", speaker_actor_ref="synthetic",
            addressee_id="ENTITY_AMORA")
        calls = []
        third = msg.attachments[2].attachment_id
        class Vision:
            available = True
            async def analyze(self,image,user_text):
                calls.append(image.attachment_id)
                if image.attachment_id == third and calls.count(third) == 1:
                    raise GroqVisionError("INCOMING_VISION_HTTP_503","synthetic",status_code=503)
                return observation()
        core.messaging.incoming_vision = Vision()
        before = await core.messaging._incoming_visual_observation(msg,decision)
        assert before["images"][2]["observation"]["status"] == "unavailable"
        core.messaging.visual_checkpoints = VisualCheckpointStore(core.database,core.clock)
        after = await core.messaging._incoming_visual_observation(msg,decision)
        assert calls == [msg.attachments[0].attachment_id,third,third]
        assert after["images"][2]["observation"]["status"] == "analyzed"
        assert after["status"] == "partial"  # missing second and omitted fourth
        rows = core.database._conn.execute(
            "SELECT payload_json FROM events WHERE event_type=?",
            ("messaging.visual.checkpoint.v1",)).fetchall()
        assert len(rows) == 2
        for row in rows:
            serialized = row["payload_json"]
            assert "content_base64" not in serialized and "source_url" not in serialized
            assert msg.incoming_images[0].image.content_base64 not in serialized
            assert all(a.attachment_id not in serialized for a in msg.attachments)
            assert msg.text not in serialized
    finally:
        core.close()


@pytest.mark.asyncio
async def test_non_image_inputs_never_touch_visual_identity_or_checkpoint_storage(tmp_path, monkeypatch):
    from amora.messaging.models import AttentionDecision, RoutingAction
    import amora.messaging.modality_service as modality
    monkeypatch.setenv("AMORA_HOME",str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind_bk(core)
        service = core.messaging
        decision = AttentionDecision(action=RoutingAction.RESPOND,score=1,
            speaker_entity_id="PERSON_BK",speaker_actor_ref="synthetic",
            addressee_id="ENTITY_AMORA")
        def unexpected(*args, **kwargs):
            pytest.fail("Non-image turn entered the visual authorization/storage path")
        monkeypatch.setattr(modality,"is_verified_owner",unexpected)
        monkeypatch.setattr(service,"_visual_checkpoint_call",unexpected)
        msg = _message(core,message_id="text-only-visual-fast-path",
            actor_id="111111111111111111",channel_type=ChannelType.DM,
            text="Me explique o funcionamento de uma imagem digital.").model_copy(
                update={"remote_processing_allowed":True})
        for attachments in ([],[AttachmentRef(attachment_id="audio",media_type="audio/ogg")],
                            [AttachmentRef(attachment_id="doc",media_type="application/pdf")]):
            candidate = msg.model_copy(update={"attachments":attachments})
            assert await service._incoming_visual_observation(candidate,decision) is None
    finally:
        core.close()


def test_visual_reference_intent_does_not_select_ordinary_or_ambiguous_images():
    from types import SimpleNamespace
    from amora.messaging.visual_reference import visual_reference_intent
    cases = [
        ("Me explique como funciona uma imagem digital.",None,None),
        ("Qual a cor da imagem anterior?",None,"latest"),
        ("Compare com a última foto.",None,"latest"),
        ("Descreva a penúltima imagem.",None,"unsupported"),
        ("Qual o texto da foto?", "native-id","reply"),
    ]
    for text,reply,expected in cases:
        msg=SimpleNamespace(text=text,reply_to_message_id=reply,reply_to_amora=False)
        assert visual_reference_intent(msg)==expected


def test_visual_reference_schema_rejects_recursive_and_unauthorized_history():
    from test_discord_incoming_vision_ch2 import observation
    from amora.cognition.visual_context import visual_context_for_request
    value={"observation_version":4,"source":"visual_reference","status":"analyzed",
           "reference_kind":"latest","observed_at":"2026-11-24T00:00:00Z","historical":observation()}
    assert visual_context_for_request(value,remote=True,private=True,mode="reply") is not None
    assert visual_context_for_request({**value,"historical":None},remote=True,private=True,mode="reply") is None
    assert visual_context_for_request({**value,"historical":value},remote=True,private=True,mode="reply") is None
    assert visual_context_for_request(value,remote=True,private=True,mode="autonomous_initiation") is None
    assert visual_context_for_request(value,remote=True,private=True,mode="scheduled_reminder") is None
