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
        assert seen[0].metadata["visual_observation"]["failure_reason"] == "provider_failed"
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
