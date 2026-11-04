from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import httpx
import pytest

from amora.cognition.certification import (
    SocialCertificationCase, SocialReplayProbe, certify_social_case,
)
from amora.cognition.models import CognitiveContext
from amora.cognition import providers_structured
from amora.cognition.providers_structured import (
    GroqCognitiveProvider, _groq_request_error_category,
)
from amora.events import PrivacyScope


class _Secrets:
    def get(self, name: str) -> str | None:
        return "TEST_KEY_DO_NOT_LEAK" if name == "GROQ_API_KEY" else None


def _context() -> CognitiveContext:
    return CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime(2026, 11, 4, tzinfo=timezone.utc),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text="Tô meio confuso sobre a mudança no carinho depois.",
        dialogue_plan={
            "act": "respond_to_disclosure",
            "depth": "medium",
            "response_moves": ["acknowledge_disclosure", "share_relational_reaction"],
            "ask_followup": False,
            "offer_help": False,
            "closing_mode": "statement",
        },
        privacy_scope=PrivacyScope.PERSONAL,
        remote_processing_allowed=True,
    )


@pytest.mark.parametrize(
    ("error", "category"),
    [
        ({"code": "json_validate_failed", "message": "PRIVATE_CONTEXT"}, "json_generation_failed"),
        ({"type": "invalid_request_error", "message": "Failed to generate JSON. PRIVATE_CONTEXT"}, "json_generation_failed"),
        ({"type": "content_filter", "message": "PRIVATE_CONTEXT"}, "explicit_content_policy_rejection"),
        ({"type": "invalid_request_error", "message": "Blocked by content filter: PRIVATE_CONTEXT"}, "explicit_content_policy_rejection"),
        ({"code": "context_length_exceeded", "message": "PRIVATE_CONTEXT"}, "context_length_exceeded"),
        ({"code": "unsupported_parameter", "message": "PRIVATE_CONTEXT"}, "unsupported_parameter"),
        ({"type": "invalid_request_error", "message": "PRIVATE_CONTEXT"}, "invalid_request_unspecified"),
        ({"type": "surprise", "message": "PRIVATE_CONTEXT"}, "unclassified"),
    ],
)
def test_groq_error_category_is_a_fixed_label_without_raw_message(error, category) -> None:
    response = httpx.Response(400, json={"error": error})
    result = _groq_request_error_category(response)
    assert result == category
    assert "PRIVATE_CONTEXT" not in result


@pytest.mark.asyncio
async def test_certification_400_reports_only_safe_provider_category(monkeypatch) -> None:
    private_marker = "PRIVATE_RELATIONSHIP_DETAILS"
    api_key = "TEST_KEY_DO_NOT_LEAK"

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/chat/completions")
        assert request.headers["Authorization"] == "Bearer " + api_key
        return httpx.Response(400, json={
            "error": {
                "type": "invalid_request_error",
                "code": "json_validate_failed",
                "message": f"Cannot generate JSON from {private_marker}; key={api_key}",
            },
        })

    original_client = httpx.AsyncClient
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(
        providers_structured.httpx, "AsyncClient",
        lambda *args, **kwargs: original_client(
            *args, transport=transport, **kwargs
        ),
    )
    case = SocialCertificationCase(
        probe=SocialReplayProbe(
            probe_id="adult_relationship_subtext",
            category="adult_relational",
            user_text=_context().user_text,
        ),
        context=_context(),
    )
    result = await certify_social_case(GroqCognitiveProvider(_Secrets()), case)
    assert result.error_code == "PROVIDER_HTTP_400"
    assert result.validated is False
    assert result.text == ""
    assert result.score is None
    assert result.transport_diagnostics["stage"] == "http_request_rejected"
    assert result.transport_diagnostics["http_status_code"] == 400
    assert result.transport_diagnostics["provider_error_category"] == "json_generation_failed"
    assert result.transport_diagnostics["prompt_chars"] > 100
    dumped = result.model_dump_json()
    assert private_marker not in dumped
    assert api_key not in dumped


@pytest.mark.asyncio
async def test_unrecognized_http_400_does_not_claim_content_filter(monkeypatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={
            "error": {
                "type": "invalid_request_error",
                "message": "An unrecognized request was rejected",
            }
        })

    original = httpx.AsyncClient
    monkeypatch.setattr(
        providers_structured.httpx, "AsyncClient",
        lambda *args, **kwargs: original(
            *args, transport=httpx.MockTransport(handler), **kwargs
        ),
    )
    ctx = _context()
    case = SocialCertificationCase(
        probe=SocialReplayProbe(probe_id="unknown_400", category="adult_relational", user_text=ctx.user_text),
        context=ctx,
    )
    report = await certify_social_case(GroqCognitiveProvider(_Secrets()), case)
    assert report.error_code == "PROVIDER_HTTP_400"
    assert report.transport_diagnostics["provider_error_category"] == "invalid_request_unspecified"
