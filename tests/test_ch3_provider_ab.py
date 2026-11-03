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
