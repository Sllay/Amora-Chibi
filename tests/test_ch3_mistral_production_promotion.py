from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import pytest

from amora.app import AmoraCore
from amora.cognition.models import CognitiveContext
from amora.cognition.provider_resilience import ResilientProviderProxy
from amora.cognition.providers_nvidia import (
    NvidiaMistralLarge4ProductionProvider,
    NvidiaMistralLarge4Provider,
)
from amora.cognition.realization import RealizationValidator
from amora.events import PrivacyScope
from amora.models import TaskClass
from amora.secrets import SecretStore
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 11, 4, 21, 0, tzinfo=timezone.utc))


def _context() -> CognitiveContext:
    return CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime(2026, 11, 4, tzinfo=timezone.utc),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text="Tu entendeu o que eu quis dizer?",
        self_model={"core_identity": {"canonical_name": "Amora"}},
        dialogue_plan={
            "act": "answer_question",
            "depth": "medium",
            "response_moves": ["answer_question"],
            "ask_followup": False,
            "offer_help": False,
            "closing_mode": "statement",
        },
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
    )


def _ranked_social(core, *, scope=PrivacyScope.PERSONAL, remote=True):
    return core.cognition.router.ranked_candidates(
        TaskClass.SOCIAL_DIALOGUE,
        privacy_scope=scope,
        remote_processing_allowed=remote,
        require_structured_output=True,
        estimated_input_tokens=900,
    )


def test_social_mistral_promoted_and_routed_first_with_groq_as_fallback(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_SECRET_NVIDIA_API_KEY", "test-nvidia-key")
    monkeypatch.setenv("AMORA_SECRET_GROQ_API_KEY", "test-groq-key")
    monkeypatch.delenv("AMORA_SOCIAL_PRIMARY", raising=False)
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        names = [x.name for x in _ranked_social(core)]
        assert names[0] == "nvidia_mistral_large_4"
        assert "groq" in names[1:]
        assert "nvidia_mistral_large_4_candidate" not in names
        prod = core.cognition.router.descriptor("nvidia_mistral_large_4")
        assert prod is not None and prod.available
        assert prod.supports_structured_output is True
        assert prod.metadata["promotion_status"] == "user_selected_social_primary"
        assert prod.metadata["structured_output_status"] == "core_validated_json_unverified_host_schema"
        assert prod.capabilities == frozenset({TaskClass.SOCIAL_DIALOGUE})
        assert core.cognition.router.descriptor("nvidia_mistral_large_4_candidate") is None

        wrapped = core.cognition._providers["nvidia_mistral_large_4"]
        assert isinstance(wrapped, ResilientProviderProxy)
        assert wrapped.max_retries == 0
        assert wrapped.max_validation_retries == 0

        assert _ranked_social(core, scope=PrivacyScope.AUTH) == ()
        assert _ranked_social(core, scope=PrivacyScope.SECRET) == ()
        assert _ranked_social(core, remote=False) == ()
        assert core.cognition.router.descriptor("nvidia_mistral_large_4").capabilities == (
            frozenset({TaskClass.SOCIAL_DIALOGUE})
        )
        # Reflection/reasoning remains separately routed, never to Mistral social.
        assert all(
            item.name != "nvidia_mistral_large_4"
            for item in core.cognition.router.ranked_candidates(
                TaskClass.REASONING, require_structured_output=True
            )
        )
    finally:
        core.close()


def test_groq_remains_primary_when_nvidia_key_is_missing(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.delenv("AMORA_SECRET_NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("AMORA_SECRET_GROQ_API_KEY", "test-groq-key")
    monkeypatch.delenv("AMORA_SOCIAL_PRIMARY", raising=False)
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        assert core.cognition.router.descriptor("nvidia_mistral_large_4") is None
        assert _ranked_social(core)[0].name == "groq"
    finally:
        core.close()


def test_explicit_operator_rollback_keeps_groq_primary(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_SECRET_NVIDIA_API_KEY", "test-nvidia-key")
    monkeypatch.setenv("AMORA_SECRET_GROQ_API_KEY", "test-groq-key")
    monkeypatch.setenv("AMORA_SOCIAL_PRIMARY", "groq")
    core = AmoraCore.bootstrap(clock=_clock())
    try:
        assert core.cognition.router.descriptor("nvidia_mistral_large_4") is None
        assert _ranked_social(core)[0].name == "groq"
    finally:
        core.close()


def test_certification_adapter_keeps_original_fail_closed_descriptor(tmp_path: Path) -> None:
    secrets = SecretStore(tmp_path / "secrets")
    candidate = NvidiaMistralLarge4Provider(secrets)
    production = NvidiaMistralLarge4ProductionProvider(secrets)
    assert candidate.descriptor.name == "nvidia_mistral_large_4_candidate"
    assert candidate.descriptor.supports_structured_output is False
    assert candidate.descriptor.priority == -100
    assert candidate.descriptor.metadata["promotion_status"] == "not_promoted"
    assert production.descriptor.name == "nvidia_mistral_large_4"
    assert production.descriptor.supports_structured_output is True
    assert production.descriptor.available is False
    assert production.max_total_seconds == 185.0
    assert production.reasoning_effort == "low"


@pytest.mark.asyncio
async def test_production_mistral_same_local_validation_with_mocked_nvidia_transport(
    tmp_path: Path, monkeypatch
) -> None:
    import json

    secrets = SecretStore(tmp_path / "secrets")
    secrets.set("NVIDIA_API_KEY", "private-nvidia-secret")
    provider = NvidiaMistralLarge4ProductionProvider(secrets)
    captured = []

    async def fake_post_chat(payload, *, api_key):
        assert api_key == "private-nvidia-secret"
        captured.append(payload)
        return (
            {"choices": [{"message": {"content": json.dumps({
                "moves": [{"kind": "answer_question", "text": "É a mudança de carinho, pai."}],
                "closing": "statement",
            })}}], "usage": {"prompt_tokens": 30, "completion_tokens": 15}},
            {},
            200,
        )

    monkeypatch.setattr(provider, "_post_chat", fake_post_chat)
    context = _context()
    raw = await provider.realize(context)
    usage = raw.pop("_usage")
    validated = RealizationValidator.parse_and_validate(context, raw)
    assert validated.text == "É a mudança de carinho, pai."
    assert usage["status_code"] == 200
    assert len(captured) == 1
    assert captured[0]["model"] == provider.model
    assert captured[0]["reasoning_effort"] == "low"
    assert captured[0]["stream"] is True
    assert captured[0]["messages"][0]["role"] == "system"
