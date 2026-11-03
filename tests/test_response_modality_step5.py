from __future__ import annotations

from pathlib import Path

import pytest

from amora.actions.builtin_capabilities import DISCORD_EXPRESSION_CAPABILITY_ID
from amora.app import AmoraCore
from amora.cognition import CognitiveRequest
from amora.events import AmoraEvent, PrivacyScope
from amora.messaging import (
    AttentionDecision,
    ChannelType,
    ExpressionKind,
    ExpressionRef,
    ExpressionSemanticProfile,
    ExternalActorRef,
    InboundMessage,
    MessagingPlatform,
    ResponseModality,
    ResponseModalitySelector,
    RoutingAction,
)
from amora.messaging.modality_service import _CapabilityAwareCognitionProxy, _CURRENT_MESSAGE
from amora.time import SimulatedClock


def _profile(
    *affect: str,
    visual: tuple[str, ...] = (),
    social: tuple[str, ...] = (),
    interpretations: tuple[str, ...] = (),
    intensity: float = 0.6,
    confidence: float = 0.95,
) -> ExpressionSemanticProfile:
    return ExpressionSemanticProfile(
        visual_concepts=list(visual),
        affect_tags=list(affect),
        social_roles=list(social),
        possible_interpretations=list(interpretations),
        register="casual",
        intensity=intensity,
        confidence=confidence,
        source="curated_semantic_memory",
    )


def _sticker(
    sticker_id: str,
    name: str,
    *tags: str,
    profile: ExpressionSemanticProfile | None = None,
) -> ExpressionRef:
    return ExpressionRef(
        kind=ExpressionKind.STICKER,
        native_id=sticker_id,
        name=name,
        tags=list(tags),
        format_type=1,
        guild_or_group_id="guild-1",
        semantic_profile=profile,
    )


def _message(
    text: str,
    *,
    message_id: str = "message-1",
    catalog: list[ExpressionRef] | None = None,
    mentions_amora: bool = True,
) -> InboundMessage:
    return InboundMessage(
        platform=MessagingPlatform.DISCORD,
        account_scope="primary",
        conversation_id="channel-1",
        message_id=message_id,
        native_message_id=message_id,
        channel_type=ChannelType.GROUP,
        sender_identifiers=[
            ExternalActorRef(
                identifier_type="discord_user_id",
                external_actor_id="111111111111111111",
            )
        ],
        text=text,
        mentions_amora=mentions_amora,
        available_expressions=catalog or [],
        timestamp=SimulatedClock().now(),
        guild_or_group_id="guild-1",
    )


def _decision() -> AttentionDecision:
    return AttentionDecision(
        action=RoutingAction.RESPOND,
        score=1.0,
        speaker_actor_ref="discord:111111111111111111",
        addressee_id="ENTITY_AMORA",
        reason_codes=["TEST_DIRECT_ATTENTION"],
    )


def _social_catalog() -> list[ExpressionRef]:
    return [
        _sticker(
            "smile-1",
            "wide-smile",
            "smile",
            "happy",
            profile=_profile(
                "joy",
                "affection",
                visual=("sorriso grande", "rindo"),
                social=("affection", "playful"),
                interpretations=("carinho alegre", "brincadeira afetuosa"),
                intensity=0.65,
            ),
        ),
        _sticker(
            "rage-1",
            "rage",
            "angry",
            profile=_profile(
                "anger",
                visual=("cara brava",),
                social=("playful",),
                intensity=0.75,
            ),
        ),
    ]


def test_natural_social_cue_can_choose_expression_without_sticker_keyword() -> None:
    selector = ResponseModalitySelector()
    message = _message("Amora, abre esse sorrisão pro pai", catalog=_social_catalog())

    decision = selector.select(message, "hehe, claro papis")

    assert "figurinha" not in message.text.casefold()
    assert "sticker" not in message.text.casefold()
    assert decision.modality in {ResponseModality.TEXT_EXPRESSION, ResponseModality.EXPRESSION}
    assert decision.expressions
    assert decision.expressions[0].native_id == "smile-1"
    assert "EXPRESSION_SELECTED_SEMANTICALLY" in decision.reason_codes
    assert "EXPRESSION_SEMANTIC_PROFILE_APPLIED" in decision.reason_codes


def test_information_need_protects_text_from_visual_hijack() -> None:
    selector = ResponseModalitySelector()
    message = _message(
        "Amora, como está a integração? teve algum erro no código?",
        catalog=_social_catalog(),
    )

    decision = selector.select(message, "A integração está estável e não apareceu erro novo.")

    assert decision.modality == ResponseModality.TEXT
    assert decision.expressions == []
    assert "INFORMATION_NEED_PROTECTS_TEXT" in decision.reason_codes


def test_learned_semantic_profile_outweighs_misleading_transport_name() -> None:
    selector = ResponseModalitySelector()
    catalog = [
        _sticker(
            "misleading-happy",
            "happy-smile",
            "happy",
            profile=_profile(
                "anger",
                visual=("olhar irritado",),
                interpretations=("ironia irritada",),
                intensity=0.8,
                confidence=1.0,
            ),
        ),
        _sticker(
            "misleading-rage",
            "rage",
            "angry",
            profile=_profile(
                "joy",
                "affection",
                visual=("sorriso aberto",),
                social=("affection",),
                interpretations=("carinho feliz",),
                intensity=0.6,
                confidence=1.0,
            ),
        ),
    ]
    message = _message("Amora, abre esse sorrisão pro pai", catalog=catalog)

    decision = selector.select(message, "hehe, papis")

    assert decision.expressions
    assert decision.expressions[0].native_id == "misleading-rage"


def test_recent_expression_penalty_prefers_fresh_semantic_match() -> None:
    selector = ResponseModalitySelector()
    shared_profile = _profile(
        "joy",
        "affection",
        visual=("sorriso",),
        social=("affection",),
        intensity=0.6,
    )
    catalog = [
        _sticker("smile-old", "smile", "happy", profile=shared_profile),
        _sticker("smile-fresh", "smile", "happy", profile=shared_profile),
    ]
    message = _message("Amora, abre esse sorrisão pro pai", catalog=catalog)

    decision = selector.select(
        message,
        "hehe, papis",
        recent_expression_ids=["smile-old"],
    )

    assert decision.expressions
    assert decision.expressions[0].native_id == "smile-fresh"


def test_silence_remains_a_competing_modality_when_explicitly_preferred() -> None:
    selector = ResponseModalitySelector()
    message = _message("Amora", catalog=[])

    decision = selector.select(message, "tô aqui", prefer_silence=True)

    assert decision.modality == ResponseModality.SILENCE
    assert decision.text == ""
    assert decision.expressions == []


def test_without_real_expression_catalog_text_stays_baseline() -> None:
    selector = ResponseModalitySelector()
    message = _message("Amora, abre esse sorrisão pro pai", catalog=[])

    decision = selector.select(message, "hehe, claro papis")

    assert decision.modality == ResponseModality.TEXT
    assert decision.expressions == []


def test_provider_safe_surface_awareness_contains_no_native_expression_ids() -> None:
    class EchoCognition:
        def observe_input(self, request):
            return request

    message = _message("Amora, abre esse sorrisão pro pai", catalog=_social_catalog())
    request = CognitiveRequest(
        text=message.text,
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        metadata={"platform": "discord", "surface": "platform_messaging"},
    )
    proxy = _CapabilityAwareCognitionProxy(EchoCognition())
    token = _CURRENT_MESSAGE.set(message)
    try:
        proxy.observe_input(request)
    finally:
        _CURRENT_MESSAGE.reset(token)

    assert request.metadata["visual_expression_available"] is True
    assert request.metadata["visual_expression_count"] == len(message.available_expressions)
    assert DISCORD_EXPRESSION_CAPABILITY_ID in request.metadata["active_capability_ids"]
    serialized = request.model_dump_json()
    assert "smile-1" not in serialized
    assert "rage-1" not in serialized
    assert "wide-smile" not in serialized


def test_surface_availability_is_turn_scoped_and_does_not_mutate_registry(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        registry = core.actions.registry.capability_registry
        before = registry.snapshot(DISCORD_EXPRESSION_CAPABILITY_ID)
        assert before is not None
        assert before.availability.value == "unavailable"

        request = CognitiveRequest(
            text="Amora, abre esse sorrisão pro pai",
            speaker_id="PERSON_BK",
            target_id="ENTITY_AMORA",
            metadata={
                "surface": "platform_messaging",
                "platform": "discord",
                "channel_type": "group",
                "active_capability_ids": [DISCORD_EXPRESSION_CAPABILITY_ID],
                "surface_available_capability_ids": [DISCORD_EXPRESSION_CAPABILITY_ID],
                "response_scoped_capability_ids": [DISCORD_EXPRESSION_CAPABILITY_ID],
                "visual_expression_available": True,
                "visual_expression_count": 2,
            },
        )
        context = core.cognition.compiler.compile(request)
        projected = [item for item in context.capabilities if item.capability_id == DISCORD_EXPRESSION_CAPABILITY_ID]
        assert len(projected) == 1
        assert projected[0].availability == "available"

        after = registry.snapshot(DISCORD_EXPRESSION_CAPABILITY_ID)
        assert after is not None
        assert after.availability.value == "unavailable"
        serialized = context.model_dump_json()
        assert "native_id" not in serialized
        assert "sticker_ids" not in serialized
    finally:
        core.close()


def test_real_messaging_layer_persists_selected_transport_expression(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        message = _message("Amora, abre esse sorrisão pro pai", catalog=_social_catalog())
        outbound = core.messaging._queue_outbound(
            message,
            _decision(),
            "hehe, claro papis",
            metadata={"transport_revision": 1},
            reason_codes=["TEST"],
        )

        assert outbound.metadata["response_modality"] in {"text+expression", "expression"}
        assert outbound.expressions
        assert outbound.expressions[0].native_id == "smile-1"
        assert outbound.metadata["transport_expressions"][0]["native_id"] == "smile-1"
        assert outbound.metadata["expression_count"] == 1

        claimed = core.messaging_store.claim_outbound(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            limit=1,
            lease_seconds=30,
        )
        assert len(claimed) == 1
        assert claimed[0].metadata["transport_expressions"][0]["native_id"] == "smile-1"
        assert claimed[0].metadata["response_modality"] in {"text+expression", "expression"}
    finally:
        core.close()


@pytest.mark.asyncio
async def test_response_scoped_capability_never_enters_autonomous_bdi(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        await core.events.publish(
            AmoraEvent(
                event_type="cognition.action_suggested",
                timestamp=core.clock.now(),
                source="cognitive_engine:capability_grounding",
                actor="ENTITY_AMORA",
                target="PERSON_BK",
                confidence=0.95,
                importance=0.7,
                payload={
                    "action_kind": "express_visually",
                    "capability_id": DISCORD_EXPRESSION_CAPABILITY_ID,
                    "capability_availability": "available",
                    "capability_grounded": True,
                    "response_scoped": True,
                    "allowed_kind": False,
                    "reason_code": "CAPABILITY_RESPONSE_SCOPED",
                    "decision": "handled_by_response_modality",
                    "model_has_execution_authority": False,
                },
                privacy_scope=PrivacyScope.PERSONAL,
            )
        )
        assert core.autonomy.state()["pending_cognitive_suggestions"] == 0
        assert core.actions.store.plan_count() == 0
        assert core.actions.store.execution_count() == 0
    finally:
        core.close()
