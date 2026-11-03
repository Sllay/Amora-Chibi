from __future__ import annotations

from datetime import datetime, timezone

import pytest

from amora.cognition.certification import SocialReplayProbe
from amora.cognition.certification_live import (
    CERTIFICATION_SESSION_ID,
    LiveCertificationConfigurationError,
    _require_provider_secrets,
    _safe_error_payload,
    build_live_social_cases,
    run_live_social_ab,
)
from amora.cognition.models import CognitiveContext
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass


class FakeSecrets:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)


class RecordingCompiler:
    def __init__(self) -> None:
        self.requests = []

    def compile(self, request):
        self.requests.append(request)
        return CognitiveContext(
            turn_id=request.turn_id,
            created_at=datetime(2026, 11, 3, tzinfo=timezone.utc),
            speaker_id=request.speaker_id,
            target_id=request.target_id,
            user_text=request.text,
            self_model={"core_identity": {"canonical_name": "Amora"}},
            relationships=[
                {
                    "source": "ENTITY_AMORA",
                    "target": request.speaker_id,
                    "relation_type": "father",
                }
            ],
            mood={"valence": 0.0, "arousal": 0.2, "dominance": 0.0},
            dialogue_plan={
                "act": "answer_question",
                "objective": "answer directly",
                "depth": "short",
                "response_moves": ["answer_question"],
                "ask_followup": False,
                "offer_help": False,
                "closing_mode": "statement",
            },
            privacy_scope=request.privacy_scope,
            remote_processing_allowed=request.remote_processing_allowed,
            reason_codes=["REMOTE_CONTEXT_ALLOWED"],
        )


class FakeCognition:
    def __init__(self, compiler: RecordingCompiler) -> None:
        self.compiler = compiler


class FakeCore:
    def __init__(self, *, secrets: FakeSecrets | None = None) -> None:
        self.secrets = secrets or FakeSecrets()
        self.compiler = RecordingCompiler()
        self.cognition = FakeCognition(self.compiler)


class TrackingRealizer:
    def __init__(self, name: str, text: str) -> None:
        self.text = text
        self.context_ids: list[int] = []
        self.descriptor = ProviderDescriptor(
            name=name,
            model=f"{name}-model",
            capabilities=frozenset({TaskClass.SOCIAL_DIALOGUE}),
            quality=0.5,
            latency_score=0.5,
            cost_score=0.0,
            provider_class=ProviderClass.REMOTE_EPHEMERAL,
            allowed_privacy_scopes=frozenset({PrivacyScope.PERSONAL}),
            supports_structured_output=True,
        )

    async def realize(self, context: CognitiveContext) -> dict[str, object]:
        self.context_ids.append(id(context))
        return {
            "moves": [{"kind": "answer_question", "text": self.text}],
            "closing": "statement",
            "_usage": {"input_tokens": 12, "output_tokens": 6},
        }


def _probes() -> tuple[SocialReplayProbe, ...]:
    return (
        SocialReplayProbe(probe_id="one", category="ordinary", user_text="Oi, como tu tá?"),
        SocialReplayProbe(probe_id="two", category="identity", user_text="Tu é minha filha mesmo?"),
    )


def test_live_case_builder_uses_read_only_remote_synthetic_session() -> None:
    core = FakeCore()
    cases = build_live_social_cases(core, probes=_probes())

    assert len(cases) == 2
    assert [case.context.user_text for case in cases] == [probe.user_text for probe in _probes()]
    assert len(core.compiler.requests) == 2
    assert all(request.session_id == CERTIFICATION_SESSION_ID for request in core.compiler.requests)
    assert all(request.remote_processing_allowed is True for request in core.compiler.requests)
    assert all(request.metadata["certification_mode"] is True for request in core.compiler.requests)
    assert all("LIVE_PROVIDER_CERTIFICATION_READ_ONLY" in case.context.reason_codes for case in cases)


def test_secret_preflight_exposes_only_missing_names() -> None:
    core = FakeCore(secrets=FakeSecrets({"GROQ_API_KEY": "groq-super-secret"}))

    with pytest.raises(LiveCertificationConfigurationError) as raised:
        _require_provider_secrets(core)

    assert raised.value.missing_secrets == ("NVIDIA_API_KEY",)
    assert "groq-super-secret" not in str(raised.value)


@pytest.mark.asyncio
async def test_live_ab_never_promotes_and_uses_same_context_per_pair() -> None:
    core = FakeCore()
    left = TrackingRealizer("qwen-baseline", "Tô bem, papis kkk.")
    right = TrackingRealizer("mistral-candidate", "Tô ótima, pai kkk.")

    result = await run_live_social_ab(
        core,
        left_provider=left,
        right_provider=right,
        probes=_probes(),
    )

    assert result.case_count == 2
    assert result.promotion_decision == "none"
    assert result.report.promotion_decision == "none"
    assert result.human_review_required is True
    assert result.report.human_review_required is True
    assert len(left.context_ids) == len(right.context_ids) == 2
    assert left.context_ids == right.context_ids
    assert "NO_AUTOMATIC_PROVIDER_PROMOTION" in result.reason_codes


def test_safe_error_payload_never_requires_secret_values() -> None:
    rendered = _safe_error_payload(
        code="CERTIFICATION_SECRET_MISSING",
        error_type="LiveCertificationConfigurationError",
        missing_secrets=["GROQ_API_KEY", "NVIDIA_API_KEY"],
    )

    assert "GROQ_API_KEY" in rendered
    assert "NVIDIA_API_KEY" in rendered
    assert "super-secret" not in rendered
