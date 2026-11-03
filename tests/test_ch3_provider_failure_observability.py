from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from amora.cognition.certification import SocialCertificationCase, SocialReplayProbe, certify_social_case
from amora.cognition.models import CognitiveContext
from amora.cognition.providers import ProviderError
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass


def _context() -> CognitiveContext:
    return CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text="oi",
        self_model={"core_identity": {"canonical_name": "Amora"}},
        relationships=[],
        mood={},
        drives={},
        dialogue_plan={
            "act": "answer_question",
            "objective": "answer directly",
            "depth": "short",
            "response_moves": ["answer_question"],
            "ask_followup": False,
            "offer_help": False,
            "closing_mode": "statement",
        },
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
    )


class FailingRealizer:
    def __init__(self, exc: Exception) -> None:
        self.exc = exc
        self.descriptor = ProviderDescriptor(
            name="fixture-provider",
            model="fixture-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=0.0,
            latency_score=0.0,
            cost_score=0.0,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
        )

    async def realize(self, context: CognitiveContext) -> dict[str, object]:
        raise self.exc


async def _run(exc: Exception):
    probe = SocialReplayProbe(probe_id="failure", category="transport", user_text="oi")
    return await certify_social_case(FailingRealizer(exc), SocialCertificationCase(probe=probe, context=_context()))


@pytest.mark.asyncio
async def test_rate_limit_is_not_hidden_as_generic_provider_error() -> None:
    result = await _run(ProviderError("rate limited", status_code=429, retry_after="12"))

    assert result.error_code == "PROVIDER_HTTP_429"
    assert "PROVIDER_RATE_LIMITED" in result.reason_codes
    assert "PROVIDER_RETRY_AFTER_PRESENT" in result.reason_codes


@pytest.mark.asyncio
async def test_bad_request_is_reported_as_http_400() -> None:
    result = await _run(ProviderError("bad request", status_code=400))

    assert result.error_code == "PROVIDER_HTTP_400"
    assert "PROVIDER_REQUEST_REJECTED" in result.reason_codes


@pytest.mark.asyncio
async def test_invalid_provider_json_is_classified_without_exposing_body() -> None:
    result = await _run(ProviderError("NVIDIA Mistral returned invalid realization JSON"))

    assert result.error_code == "PROVIDER_RESPONSE_JSON_INVALID"
    assert "PROVIDER_RESPONSE_INVALID" in result.reason_codes
