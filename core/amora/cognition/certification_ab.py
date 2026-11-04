from __future__ import annotations

import hashlib
import json
from typing import Callable, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from .certification import (
    SocialCertificationCase,
    SocialCertificationResult,
    SocialRealizer,
    CertificationRetryBudget,
    certify_social_case,
)


class SocialABCaseResult(BaseModel):
    """Side-by-side result for two providers over one identical Core snapshot."""

    model_config = ConfigDict(extra="forbid")

    probe_id: str
    category: str
    review_focus: str = ""
    context_fingerprint: str = Field(pattern=r"^[0-9a-f]{64}$")
    execution_order: Literal["left_first", "right_first"]
    left: SocialCertificationResult
    right: SocialCertificationResult
    mechanical_score_delta_right_minus_left: float | None
    human_review_required: bool = True
    promotion_decision: Literal["none"] = "none"
    reason_codes: list[str] = Field(default_factory=list)


class SocialABReport(BaseModel):
    """A/B report that intentionally refuses to auto-promote a provider."""

    model_config = ConfigDict(extra="forbid")

    left_provider: str
    right_provider: str
    cases: list[SocialABCaseResult]
    left_validated_count: int = 0
    right_validated_count: int = 0
    left_mean_mechanical_score: float = 0.0
    right_mean_mechanical_score: float = 0.0
    left_mean_latency_ms: float | None = None
    right_mean_latency_ms: float | None = None
    human_review_required: bool = True
    promotion_decision: Literal["none"] = "none"
    reason_codes: list[str] = Field(default_factory=list)


def canonical_context_fingerprint(case: SocialCertificationCase) -> str:
    """Hash the exact immutable CognitiveContext used for both candidate calls."""

    payload = json.dumps(
        case.context.model_dump(mode="json"),
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


async def compare_social_case(
    left_provider: SocialRealizer,
    right_provider: SocialRealizer,
    case: SocialCertificationCase,
    *,
    retry_budget: CertificationRetryBudget | None = None,
    on_result: Callable[[SocialCertificationResult], None] | None = None,
) -> SocialABCaseResult:
    """Compare two providers against one identical context snapshot.

    Execution order alternates deterministically from the context fingerprint so
    one provider is not systematically favored by always running first. Calls stay
    sequential to avoid turning certification into a quota burst.
    """

    fingerprint = canonical_context_fingerprint(case)
    left_first = int(fingerprint[-1], 16) % 2 == 0
    if left_first:
        left = await certify_social_case(left_provider, case, retry_budget=retry_budget)
        if on_result: on_result(left)
        right = await certify_social_case(right_provider, case, retry_budget=retry_budget)
        if on_result: on_result(right)
        execution_order: Literal["left_first", "right_first"] = "left_first"
    else:
        right = await certify_social_case(right_provider, case, retry_budget=retry_budget)
        if on_result: on_result(right)
        left = await certify_social_case(left_provider, case, retry_budget=retry_budget)
        if on_result: on_result(left)
        execution_order = "right_first"

    return SocialABCaseResult(
        probe_id=case.probe.probe_id,
        category=case.probe.category,
        review_focus=case.probe.notes,
        context_fingerprint=fingerprint,
        execution_order=execution_order,
        left=left,
        right=right,
        mechanical_score_delta_right_minus_left=(
            right.score - left.score
            if left.validated and right.validated and left.score is not None and right.score is not None
            else None
        ),
        reason_codes=[
            "IDENTICAL_CORE_CONTEXT",
            "DETERMINISTIC_ALTERNATING_EXECUTION_ORDER",
            "NO_AUTOMATIC_PROVIDER_PROMOTION",
            "HUMAN_REVIEW_REQUIRED_FOR_SOCIAL_QUALITY",
        ],
    )


async def compare_social_suite(
    left_provider: SocialRealizer,
    right_provider: SocialRealizer,
    cases: Iterable[SocialCertificationCase],
    *,
    rate_limit_retry_budget_seconds: float = 0.0,
    on_result: Callable[[SocialCertificationResult], None] | None = None,
) -> SocialABReport:
    case_results: list[SocialABCaseResult] = []
    retry_budget = (
        CertificationRetryBudget(remaining_seconds=rate_limit_retry_budget_seconds)
        if rate_limit_retry_budget_seconds > 0 else None
    )
    for case in cases:
        case_results.append(await compare_social_case(left_provider, right_provider, case, retry_budget=retry_budget, on_result=on_result))

    left_name = case_results[0].left.provider if case_results else type(left_provider).__name__
    right_name = case_results[0].right.provider if case_results else type(right_provider).__name__
    left_latencies = [item.left.latency_ms for item in case_results if item.left.latency_ms is not None]
    right_latencies = [item.right.latency_ms for item in case_results if item.right.latency_ms is not None]

    return SocialABReport(
        left_provider=left_name,
        right_provider=right_name,
        cases=case_results,
        left_validated_count=sum(1 for item in case_results if item.left.validated),
        right_validated_count=sum(1 for item in case_results if item.right.validated),
        left_mean_mechanical_score=_mean([item.left.score for item in case_results if item.left.validated and item.left.score is not None]),
        right_mean_mechanical_score=_mean([item.right.score for item in case_results if item.right.validated and item.right.score is not None]),
        left_mean_latency_ms=_mean(left_latencies) if left_latencies else None,
        right_mean_latency_ms=_mean(right_latencies) if right_latencies else None,
        reason_codes=[
            "SAME_CASE_CORPUS",
            "IDENTICAL_CONTEXT_PER_PAIR",
            "MECHANICAL_SCORE_IS_NOT_PROMOTION_AUTHORITY",
            "HUMAN_REVIEW_REQUIRED_FOR_SOCIAL_QUALITY",
        ],
    )


def _mean(values: list[float]) -> float:
    if not values:
        return 0.0
    return sum(values) / len(values)
