from __future__ import annotations

import base64
import hashlib
import json
from pathlib import Path

import httpx
import pytest

from amora.app import AmoraCore
from amora.events import PrivacyScope
from amora.messaging import (
    AttentionDecision,
    ChannelType,
    ExpressionKind,
    ExpressionRef,
    ExternalActorRef,
    InboundMessage,
    MessagingPlatform,
    RoutingAction,
)
from amora.messaging.groq_vision import GroqQwenVisionAnalyzer, GroqVisionError
from amora.messaging.visual_enrichment import VisualEnrichmentStatus
from amora.messaging.visual_routes import configure_visual_analyzer
from amora.secrets import SecretStore
from amora.time import SimulatedClock


PNG = b"\x89PNG\r\n\x1a\n" + b"groq-vision-contract"
MODEL = "qwen/qwen3.8-27b"


def _valid_profile() -> dict[str, object]:
    return {
        "short_description": "Chibi sorrindo de olhos semicerrados em tom de zoeira.",
        "visual_concepts": ["chibi", "sorriso", "olhos semicerrados"],
        "affect_tags": ["joy", "playful"],
        "social_roles": ["playful", "teasing"],
        "possible_interpretations": ["rir junto", "provocar de forma leve"],
        "register": "playful",
        "intensity": 0.78,
        "valence": 0.82,
        "arousal": 0.66,
        "confidence": 0.94,
        "source": "vision_model",
    }


def _secrets(tmp_path: Path) -> SecretStore:
    store = SecretStore(tmp_path / "secrets")
    store.set("GROQ_API_KEY", "unit-test-groq-token")
    return store


def _success_transport(captured: list[dict[str, object]], calls: list[int]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        calls[0] += 1
        payload = json.loads(request.content.decode("utf-8"))
        captured.append(payload)
        assert request.headers.get("authorization") == "Bearer unit-test-groq-token"
        assert "unit-test-groq-token" not in request.content.decode("utf-8")
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": json.dumps(_valid_profile(), ensure_ascii=False),
                        }
                    }
                ]
            },
        )

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_adapter_sends_one_image_with_strict_schema_and_validates_locally(tmp_path: Path) -> None:
    captured: list[dict[str, object]] = []
    calls = [0]
    analyzer = GroqQwenVisionAnalyzer(
        _secrets(tmp_path),
        transport=_success_transport(captured, calls),
    )

    result = await analyzer.analyze(
        content=PNG,
        mime="image/png",
        name="sorriso",
        description="metadata de transporte",
        tags=("smile", "happy"),
    )

    assert calls[0] == 1
    payload = captured[0]
    assert payload["model"] == MODEL
    assert payload["stream"] is False
    assert payload["reasoning_effort"] == "high"
    assert payload["reasoning_format"] == "hidden"
    response_format = payload["response_format"]
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["strict"] is True
    schema = response_format["json_schema"]["schema"]
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    content = payload["messages"][0]["content"]
    image = next(item for item in content if item["type"] == "image_url")
    assert image["image_url"]["url"].startswith("data:image/png;base64,")
    assert base64.b64decode(image["image_url"]["url"].split(",", 1)[1]) == PNG
    assert result.provider == "groq"
    assert result.model == MODEL
    assert result.profile.source == "vision_model"
    assert result.profile.short_description.startswith("Chibi sorrindo")


@pytest.mark.asyncio
async def test_adapter_rejects_invalid_semantic_profile_after_provider_schema_boundary(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        invalid = _valid_profile()
        invalid["confidence"] = 4.2
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": json.dumps(invalid)}}]},
        )

    analyzer = GroqQwenVisionAnalyzer(_secrets(tmp_path), transport=httpx.MockTransport(handler))

    with pytest.raises(GroqVisionError) as exc:
        await analyzer.analyze(
            content=PNG,
            mime="image/png",
            name=None,
            description=None,
            tags=(),
        )

    assert exc.value.code == "GROQ_VISION_INVALID_PROFILE"


def test_adapter_defers_animated_formats_until_renderer_contract_exists(tmp_path: Path) -> None:
    analyzer = GroqQwenVisionAnalyzer(_secrets(tmp_path))
    assert analyzer.supports_mime("image/png") is True
    assert analyzer.supports_mime("image/jpeg") is True
    assert analyzer.supports_mime("image/webp") is True
    assert analyzer.supports_mime("image/gif") is False
    assert analyzer.supports_mime("application/json") is False


def test_route_configuration_is_optional_and_secret_backed(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        assert core.messaging.visual_enrichment.analyzer is None
        assert configure_visual_analyzer(core) is False
        assert core.messaging.visual_enrichment.analyzer is None

        core.secrets.set("GROQ_API_KEY", "unit-test-groq-token")
        assert configure_visual_analyzer(core) is True
        assert isinstance(core.messaging.visual_enrichment.analyzer, GroqQwenVisionAnalyzer)
    finally:
        core.close()


def _sticker(*, description: str) -> ExpressionRef:
    return ExpressionRef(
        kind=ExpressionKind.STICKER,
        native_id="123456789012345678",
        name="sorriso",
        description=description,
        tags=["smile", "happy"],
        format_type=1,
        guild_or_group_id="guild-1",
    )


def _message(sticker: ExpressionRef, *, message_id: str) -> InboundMessage:
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
        text="",
        mentions_amora=True,
        expressions=[sticker],
        available_expressions=[sticker],
        timestamp=SimulatedClock().now(),
        guild_or_group_id="guild-1",
        remote_processing_allowed=True,
        privacy_scope=PrivacyScope.PERSONAL,
    )


def _decision() -> AttentionDecision:
    return AttentionDecision(
        action=RoutingAction.RESPOND,
        score=1.0,
        speaker_actor_ref="discord:111111111111111111",
        addressee_id="ENTITY_AMORA",
        reason_codes=["TEST"],
    )


@pytest.mark.asyncio
async def test_real_adapter_service_pipeline_persists_semantics_and_reuses_same_sha(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    captured: list[dict[str, object]] = []
    calls = [0]
    try:
        core.secrets.set("GROQ_API_KEY", "unit-test-groq-token")
        service = core.messaging.visual_enrichment
        service.analyzer = GroqQwenVisionAnalyzer(
            core.secrets,
            transport=_success_transport(captured, calls),
        )

        first_message = _message(_sticker(description="metadata inicial"), message_id="m1")
        core.messaging.expression_memory.observe_relevant(first_message, _decision())
        service.observe_policy(first_message, _decision())
        first_claim = service.claim(platform=MessagingPlatform.DISCORD, account_scope="primary")[0]
        first = await service.ingest_base64(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            scope_id=first_claim.record.scope_id,
            kind=first_claim.record.kind,
            native_id=first_claim.record.native_id,
            expected_fingerprint=first_claim.record.fingerprint,
            claim_token=first_claim.claim_token,
            content_mime="image/png",
            content_base64=base64.b64encode(PNG).decode("ascii"),
        )

        assert first.status == VisualEnrichmentStatus.ENRICHED
        assert first.content_fingerprint == hashlib.sha256(PNG).hexdigest()
        assert calls[0] == 1

        record = core.messaging.expression_memory.get(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            scope_id="guild-1",
            kind="sticker",
            native_id="123456789012345678",
        )
        assert record is not None
        assert record.semantic_revision == 1
        assert record.visual_provider == "groq"
        assert record.visual_model == MODEL
        assert record.content_fingerprint == hashlib.sha256(PNG).hexdigest()

        with core.database._lock:
            row = core.database._conn.execute(
                "SELECT semantic_json FROM perceptual_expression_assets WHERE native_id=?",
                ("123456789012345678",),
            ).fetchone()
        semantic = json.loads(row["semantic_json"])
        assert semantic["source"] == "vision_model"
        assert semantic["confidence"] == pytest.approx(0.94)

        revised_message = _message(_sticker(description="metadata revisada"), message_id="m2")
        core.messaging.expression_memory.observe_relevant(revised_message, _decision())
        service.observe_policy(revised_message, _decision())
        second_claim = service.claim(platform=MessagingPlatform.DISCORD, account_scope="primary")[0]
        second = await service.ingest_base64(
            platform=MessagingPlatform.DISCORD,
            account_scope="primary",
            scope_id=second_claim.record.scope_id,
            kind=second_claim.record.kind,
            native_id=second_claim.record.native_id,
            expected_fingerprint=second_claim.record.fingerprint,
            claim_token=second_claim.claim_token,
            content_mime="image/png",
            content_base64=base64.b64encode(PNG).decode("ascii"),
        )

        assert second.status == VisualEnrichmentStatus.REUSED
        assert second.semantic_revision == 1
        assert calls[0] == 1
    finally:
        core.close()
