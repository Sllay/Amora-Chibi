from __future__ import annotations

import argparse
import asyncio
import json
import sys
from typing import Any, Callable, Iterable, Literal

from pydantic import BaseModel, ConfigDict, Field

from amora.events import PrivacyScope

from .certification import SOCIAL_SURFACE_REPLAY_V1, SocialCertificationCase, SocialCertificationResult, SocialReplayProbe, SocialRealizer
from .certification_ab import SocialABReport, compare_social_suite
from .models import CognitiveRequest
from .providers_nvidia import NvidiaMistralLarge4Provider
from .providers_structured import GroqCognitiveProvider


CORPUS_ID = "SOCIAL_SURFACE_REPLAY_V1"
EXPRESSIVE_CORPUS_ID = "SOCIAL_EXPRESSIVE4_V1"
CERTIFICATION_SESSION_ID = "certification:ch3:social_surface_replay_v1"
EXPRESSIVE_SESSION_ID = "certification:ch3:social_expressive4_v1"


def _session_for_corpus(corpus_id: str) -> str:
    """Only fixed synthetic read-only sessions are permitted in certification."""
    if corpus_id == CORPUS_ID:
        return CERTIFICATION_SESSION_ID
    if corpus_id == EXPRESSIVE_CORPUS_ID:
        return EXPRESSIVE_SESSION_ID
    raise ValueError("unsupported social certification corpus")


class LiveCertificationConfigurationError(RuntimeError):
    """Fail-closed configuration error that exposes names, never secret values."""

    def __init__(self, missing_secrets: Iterable[str]) -> None:
        self.missing_secrets = tuple(sorted(set(missing_secrets)))
        super().__init__("required provider secrets are not configured")


class LiveSocialABReport(BaseModel):
    """Safe one-shot envelope for CH3 live social provider certification."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["amora.provider-certification.social-ab.v1"] = (
        "amora.provider-certification.social-ab.v1"
    )
    corpus_id: Literal["SOCIAL_SURFACE_REPLAY_V1", "SOCIAL_EXPRESSIVE4_V1"] = CORPUS_ID
    speaker_id: str
    target_id: str = "ENTITY_AMORA"
    session_id: str = CERTIFICATION_SESSION_ID
    case_count: int = Field(ge=1)
    remote_processing: bool = True
    read_only_context_compilation: bool = True
    human_review_required: bool = True
    promotion_decision: Literal["none"] = "none"
    report: SocialABReport
    reason_codes: list[str] = Field(default_factory=list)


def build_live_social_cases(
    core: Any,
    *,
    speaker_id: str = "PERSON_BK",
    target_id: str = "ENTITY_AMORA",
    probes: Iterable[SocialReplayProbe] = SOCIAL_SURFACE_REPLAY_V1,
    corpus_id: str = CORPUS_ID,
) -> list[SocialCertificationCase]:
    """Compile the fixed replay through the live canonical ContextCompiler without writes.

    The synthetic session id deliberately has no persisted conversation history. The
    compiler may still project canonical identity, self state, relationships, memory,
    world state, emotion and capabilities through its normal remote-privacy gates.
    No input message/journal/promotion state is persisted by this function.
    """

    cases: list[SocialCertificationCase] = []
    session_id = _session_for_corpus(corpus_id)
    compiler = core.cognition.compiler
    for probe in probes:
        request = CognitiveRequest(
            text=probe.user_text,
            speaker_id=speaker_id,
            target_id=target_id,
            session_id=session_id,
            privacy_scope=PrivacyScope.PERSONAL,
            remote_processing_allowed=True,
            metadata={
                "certification_mode": True,
                "certification_corpus": corpus_id,
                "certification_probe_id": probe.probe_id,
            },
        )
        context = compiler.compile(request)
        if "LIVE_PROVIDER_CERTIFICATION_READ_ONLY" not in context.reason_codes:
            context.reason_codes.append("LIVE_PROVIDER_CERTIFICATION_READ_ONLY")
        cases.append(SocialCertificationCase(probe=probe, context=context))
    return cases


def _require_provider_secrets(core: Any) -> None:
    required = ("GROQ_API_KEY", "NVIDIA_API_KEY")
    missing = [name for name in required if core.secrets.get(name) is None]
    if missing:
        raise LiveCertificationConfigurationError(missing)


async def run_live_social_ab(
    core: Any,
    *,
    speaker_id: str = "PERSON_BK",
    target_id: str = "ENTITY_AMORA",
    left_provider: SocialRealizer | None = None,
    right_provider: SocialRealizer | None = None,
    probes: Iterable[SocialReplayProbe] = SOCIAL_SURFACE_REPLAY_V1,
    corpus_id: str = CORPUS_ID,
    on_result: Callable[[SocialCertificationResult], None] | None = None,
) -> LiveSocialABReport:
    """Run Qwen baseline × Mistral candidate over identical read-only Core contexts.

    Injected providers are supported for deterministic tests. The production path
    constructs the current structured Groq/Qwen baseline and the NVIDIA-hosted
    Mistral candidate only after secret-name preflight succeeds.
    """

    if left_provider is None or right_provider is None:
        _require_provider_secrets(core)
    left = left_provider or GroqCognitiveProvider(core.secrets)
    right = right_provider or NvidiaMistralLarge4Provider(core.secrets)

    cases = build_live_social_cases(
        core,
        speaker_id=speaker_id,
        target_id=target_id,
        probes=probes,
        corpus_id=corpus_id,
    )
    # Shared quota-wait budget is bounded below the Shell's 300s job timeout.
    # Honor provider Retry-After only when supplied, never retry HTTP 400.
    report = await compare_social_suite(
        left, right, cases, rate_limit_retry_budget_seconds=120.0, on_result=on_result
    )
    return LiveSocialABReport(
        corpus_id=corpus_id,
        session_id=_session_for_corpus(corpus_id),
        speaker_id=speaker_id,
        target_id=target_id,
        case_count=len(cases),
        report=report,
        reason_codes=[
            "FIXED_SOCIAL_REPLAY_CORPUS",
            *(
                ["FOCUSED_EXPRESSIVE4_NOT_FULL_CERTIFICATION"]
                if corpus_id == EXPRESSIVE_CORPUS_ID else []
            ),
            "CANONICAL_CONTEXT_COMPILER",
            "SYNTHETIC_SESSION_NO_CONVERSATION_WRITE",
            "IDENTICAL_CONTEXT_PER_PROVIDER_PAIR",
            "SEQUENTIAL_PROVIDER_EXECUTION",
            "NO_AUTOMATIC_PROVIDER_PROMOTION",
            "HUMAN_REVIEW_REQUIRED",
        ],
    )


def _safe_error_payload(*, code: str, error_type: str, **extra: object) -> str:
    payload: dict[str, object] = {"ok": False, "error_code": code, "error_type": error_type}
    payload.update(extra)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m amora.cognition.certification_live",
        description="Run CH3 live Qwen × Mistral social certification without promotion.",
    )
    parser.add_argument("--speaker-id", default="PERSON_BK")
    parser.add_argument("--target-id", default="ENTITY_AMORA")
    parser.add_argument("--compact", action="store_true", help="emit compact JSON")
    args = parser.parse_args(argv)

    # Delayed import keeps the certification module independently testable and
    # avoids app bootstrap/circular imports during ordinary cognition imports.
    from amora.app import AmoraCore

    core = AmoraCore.bootstrap()
    try:
        result = asyncio.run(
            run_live_social_ab(
                core,
                speaker_id=args.speaker_id,
                target_id=args.target_id,
            )
        )
    except LiveCertificationConfigurationError as exc:
        print(
            _safe_error_payload(
                code="CERTIFICATION_SECRET_MISSING",
                error_type=type(exc).__name__,
                missing_secrets=list(exc.missing_secrets),
            ),
            file=sys.stderr,
        )
        return 2
    except Exception as exc:  # pragma: no cover - live transport boundary
        status_code = getattr(exc, "status_code", None)
        extra: dict[str, object] = {}
        if isinstance(status_code, int):
            extra["status_code"] = status_code
        print(
            _safe_error_payload(
                code="CERTIFICATION_RUN_FAILED",
                error_type=type(exc).__name__,
                **extra,
            ),
            file=sys.stderr,
        )
        return 1

    if args.compact:
        print(result.model_dump_json())
    else:
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through module invocation
    raise SystemExit(main())
