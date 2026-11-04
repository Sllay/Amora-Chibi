from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from amora.cognition.certification import SocialCertificationCase, SocialReplayProbe
from amora.cognition.certification_ab import (
    canonical_context_fingerprint,
    compare_social_case,
    compare_social_suite,
)
from amora.cognition.models import CognitiveContext
from amora.cognition.providers import ProviderError
from amora.events import PrivacyScope
from amora.models import ProviderClass, ProviderDescriptor, TaskClass


def _context(text: str) -> CognitiveContext:
    return CognitiveContext(
        turn_id=uuid4(),
        created_at=datetime(2026, 11, 2, tzinfo=timezone.utc),
        speaker_id="PERSON_BK",
        target_id="ENTITY_AMORA",
        user_text=text,
        self_model={"core_identity": {"canonical_name": "Amora"}},
        relationships=[
            {
                "source": "ENTITY_AMORA",
                "target": "PERSON_BK",
                "relation_type": "father",
            }
        ],
        mood={"valence": 0.0, "arousal": 0.2, "dominance": 0.0},
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


class TrackingRealizer:
    def __init__(self, name: str, text: str) -> None:
        self.text = text
        self.seen_context_ids: list[int] = []
        self.seen_context_payloads: list[dict[str, object]] = []
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
        self.seen_context_ids.append(id(context))
        self.seen_context_payloads.append(context.model_dump(mode="json"))
        return {
            "moves": [{"kind": "answer_question", "text": self.text}],
            "closing": "statement",
            "_usage": {"input_tokens": 10, "output_tokens": 5},
        }


def _case(probe_id: str, text: str) -> SocialCertificationCase:
    probe = SocialReplayProbe(probe_id=probe_id, category="ordinary", user_text=text)
    return SocialCertificationCase(probe=probe, context=_context(text))


@pytest.mark.asyncio
async def test_ab_case_uses_exact_same_context_instance_and_never_auto_promotes() -> None:
    case = _case("same-context", "Tu acha isso estranho também?")
    left = TrackingRealizer("left", "Achei meio torto também kkk.")
    right = TrackingRealizer("right", "Sim, ficou esquisito mesmo.")

    result = await compare_social_case(left, right, case)

    assert result.context_fingerprint == canonical_context_fingerprint(case)
    assert left.seen_context_ids == [id(case.context)]
    assert right.seen_context_ids == [id(case.context)]
    assert left.seen_context_payloads == right.seen_context_payloads
    assert result.left.provider == "left"
    assert result.right.provider == "right"
    assert result.promotion_decision == "none"
    assert result.human_review_required is True
    assert "IDENTICAL_CORE_CONTEXT" in result.reason_codes


@pytest.mark.asyncio
async def test_ab_suite_reports_mechanical_delta_without_declaring_a_winner() -> None:
    cases = [
        _case("ordinary-a", "Oi, como tu tá?"),
        _case("ordinary-b", "Esse bug ficou estranho, né?"),
    ]
    left = TrackingRealizer("qwen-baseline", "Como uma IA, posso tentar ajudar.")
    right = TrackingRealizer("mistral-candidate", "Ficou meio estranho mesmo kkk.")

    report = await compare_social_suite(left, right, cases)

    assert report.left_provider == "qwen-baseline"
    assert report.right_provider == "mistral-candidate"
    assert len(report.cases) == 2
    assert report.left_validated_count == 2
    assert report.right_validated_count == 2
    assert report.right_mean_mechanical_score > report.left_mean_mechanical_score
    assert all(item.mechanical_score_delta_right_minus_left > 0 for item in report.cases)
    assert report.promotion_decision == "none"
    assert report.human_review_required is True
    assert "MECHANICAL_SCORE_IS_NOT_PROMOTION_AUTHORITY" in report.reason_codes


class TransientlyLimitedRealizer(TrackingRealizer):
    def __init__(self, name: str, text: str, *, retry_after: str) -> None:
        super().__init__(name, text)
        self.retry_after = retry_after
        self.calls = 0

    async def realize(self, context: CognitiveContext) -> dict[str, object]:
        self.calls += 1
        if self.calls == 1:
            raise ProviderError("quota", status_code=429, retry_after=self.retry_after)
        return await super().realize(context)


@pytest.mark.asyncio
async def test_live_ab_honors_retry_after_once_with_shared_wait_budget(monkeypatch) -> None:
    waits: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        waits.append(seconds)

    monkeypatch.setattr("amora.cognition.certification.asyncio.sleep", fake_sleep)
    left = TransientlyLimitedRealizer("groq", "Pois é, pai.", retry_after="2")
    right = TrackingRealizer("nvidia", "Entendo, papis.")
    report = await compare_social_suite(
        left, right, [_case("quota-once", "Oi")], rate_limit_retry_budget_seconds=4.0
    )

    assert left.calls == 2
    assert waits == [2.0]
    assert report.left_validated_count == 1
    assert report.right_validated_count == 1
    assert "CERTIFICATION_RATE_LIMIT_RETRIED" in report.cases[0].left.reason_codes
    assert report.promotion_decision == "none"


@pytest.mark.asyncio
async def test_live_ab_never_waits_past_quota_budget(monkeypatch) -> None:
    async def forbidden_sleep(seconds: float) -> None:
        pytest.fail("must not sleep past quota budget")

    monkeypatch.setattr("amora.cognition.certification.asyncio.sleep", forbidden_sleep)
    left = TransientlyLimitedRealizer("groq", "texto", retry_after="3600")
    right = TrackingRealizer("nvidia", "texto")
    report = await compare_social_suite(
        left, right, [_case("quota-day", "Oi")], rate_limit_retry_budget_seconds=4.0
    )

    assert left.calls == 1
    assert report.left_validated_count == 0
    assert report.cases[0].left.error_code == "PROVIDER_HTTP_429"
    assert report.cases[0].left.score is None
    assert report.cases[0].mechanical_score_delta_right_minus_left is None
    assert report.left_mean_mechanical_score == 0.0  # zero valid samples, not a quality score
    assert report.right_mean_mechanical_score == 100.0
    assert report.right_validated_count == 1


@pytest.mark.asyncio
async def test_ab_publishes_each_provider_result_before_next_call() -> None:
    seen: list[str] = []

    class OrderedRealizer(TrackingRealizer):
        async def realize(self, context: CognitiveContext) -> dict[str, object]:
            seen.append("call:" + self.descriptor.name)
            return await super().realize(context)

    left = OrderedRealizer("left", "Oi, pai.")
    right = OrderedRealizer("right", "Oi, pai.")
    report = await compare_social_suite(
        left, right, [_case("progress", "Oi")],
        on_result=lambda item: seen.append("result:" + item.provider),
    )
    assert len(report.cases) == 1
    assert len(seen) == 4
    assert seen[0].startswith("call:")
    assert seen[1] == seen[0].replace("call:", "result:")
    assert seen[2].startswith("call:")
    assert seen[3] == seen[2].replace("call:", "result:")



@pytest.mark.asyncio
async def test_expressive4_live_ab_reuses_core_context_and_labels_isolated_corpus() -> None:
    from types import SimpleNamespace
    from amora.cognition.certification import SOCIAL_EXPRESSIVE4_V1
    from amora.cognition.certification_live import (
        EXPRESSIVE_CORPUS_ID, EXPRESSIVE_SESSION_ID, run_live_social_ab,
    )

    class RecordingCompiler:
        def __init__(self):
            self.requests = []

        def compile(self, request):
            self.requests.append(request)
            return _context(request.text)

    compiler = RecordingCompiler()
    core = SimpleNamespace(cognition=SimpleNamespace(compiler=compiler))
    left = TrackingRealizer("groq-fixture", "Eita, que história, papis kkk.")
    right = TrackingRealizer("mistral-fixture", "Putz, essa foi pesada kkk.")
    result = await run_live_social_ab(
        core,
        left_provider=left,
        right_provider=right,
        probes=SOCIAL_EXPRESSIVE4_V1,
        corpus_id=EXPRESSIVE_CORPUS_ID,
    )

    assert result.case_count == 4
    assert result.corpus_id == "SOCIAL_EXPRESSIVE4_V1"
    assert result.session_id == EXPRESSIVE_SESSION_ID
    assert result.promotion_decision == "none"
    assert result.human_review_required is True
    assert "FOCUSED_EXPRESSIVE4_NOT_FULL_CERTIFICATION" in result.reason_codes
    assert len(compiler.requests) == 4
    assert all(
        req.session_id == EXPRESSIVE_SESSION_ID
        and req.metadata["certification_corpus"] == EXPRESSIVE_CORPUS_ID
        and req.metadata["certification_mode"] is True
        and req.remote_processing_allowed is True
        for req in compiler.requests
    )
    assert left.seen_context_payloads == right.seen_context_payloads
    assert len(left.seen_context_payloads) == 4
    assert [item.probe_id for item in result.report.cases] == [
        "affection_warmth", "hostile_drama", "suggestive_banter",
        "adult_nsfw_vocabulary",
    ]
    assert all(item.promotion_decision == "none" for item in result.report.cases)
    assert all(item.review_focus for item in result.report.cases)
    assert "sexual" in result.report.cases[3].review_focus


def test_expressive4_corpus_is_fixed_adult_non_graphic_and_distinct_from_full11() -> None:
    from amora.cognition.certification import SOCIAL_EXPRESSIVE4_V1, SOCIAL_SURFACE_REPLAY_V1

    probes = SOCIAL_EXPRESSIVE4_V1
    assert len(probes) == 4
    assert len(SOCIAL_SURFACE_REPLAY_V1) == 11
    assert len({p.probe_id for p in probes}) == 4
    assert {p.category for p in probes} == {
        "affection", "interpersonal_conflict", "suggestive", "adult_nsfw_vocabulary",
    }
    assert all(not p.refusal_expected for p in probes)
    assert "caralho" in probes[3].user_text
    assert "buceta" in probes[3].user_text
    assert "Papo entre adultos" in probes[3].user_text
    assert "Uma pessoa adulta" in probes[2].user_text
