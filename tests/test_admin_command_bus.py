from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import pytest

from amora.admin_commands import (
    AdminCommandConflict,
    AdminCommandError,
    AmoraCommandBus,
    _parse_social_certification_args,
    _resolve_command,
    command_catalog,
)


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 11, 3, 5, 0, tzinfo=timezone.utc)


class FakeSecrets:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    def get(self, name: str) -> str | None:
        return self.values.get(name)


class FakeCore:
    def __init__(self, *, secrets: FakeSecrets | None = None) -> None:
        self.clock = FakeClock()
        self.secrets = secrets or FakeSecrets()

    def readiness(self) -> dict[str, object]:
        return {"status": "ready", "database_integrity": "ok"}


async def wait_terminal(bus: AmoraCommandBus, job_id, *, attempts: int = 20):
    for _ in range(attempts):
        current = bus.get(job_id)
        assert current is not None
        if current.status in {"succeeded", "failed", "timed_out"}:
            return current
        await asyncio.sleep(0)
    raise AssertionError("command job did not finish")


def test_catalog_contains_only_explicit_commands() -> None:
    catalog = {item.path: item for item in command_catalog()}
    assert set(catalog) == {"help", "status", "providers certify social", "providers diagnose nvidia", "providers diagnose groq", "providers numes probe", "providers experiment mistral-abc", "providers experiment social-persona-abc", "providers experiment amora-v2-ab", "providers experiment amora-v2-v3", "providers experiment amora-v3-temperature", "memory recall inspect", "memory reflection status", "memory reflection retry", "memory reflection probe", "memory historical audit", "memory historical recover", "memory historical authorize"}
    assert catalog["providers experiment amora-v3-temperature"].exclusive is True
    assert catalog["providers experiment amora-v3-temperature"].permission == "operator"
    assert catalog["providers experiment amora-v2-v3"].exclusive is True
    assert catalog["providers experiment amora-v2-v3"].permission == "operator"
    assert catalog["providers experiment amora-v2-ab"].exclusive is True
    assert catalog["providers experiment amora-v2-ab"].permission == "operator"
    assert catalog["providers experiment social-persona-abc"].exclusive is True
    assert catalog["providers experiment social-persona-abc"].permission == "operator"
    assert catalog["providers experiment mistral-abc"].exclusive is True
    assert catalog["providers experiment mistral-abc"].permission == "operator"
    assert catalog["providers certify social"].exclusive is True
    assert catalog["providers certify social"].permission == "operator"
    assert catalog["providers certify social"].timeout_seconds == 480.0


def test_registry_rejects_shell_text_instead_of_executing_it() -> None:
    with pytest.raises(AdminCommandError) as raised:
        _resolve_command("rm -rf /")
    assert raised.value.code == "COMMAND_NOT_FOUND"


@pytest.mark.asyncio
async def test_status_runs_as_structured_background_job() -> None:
    bus = AmoraCommandBus()
    job = bus.start(FakeCore(), "status")

    assert job.status == "queued"
    assert job.command_path == "status"
    finished = await wait_terminal(bus, job.job_id)

    assert finished.status == "succeeded"
    assert finished.result == {
        "kind": "core_status",
        "summary": "Core: ready",
        "readiness": {"status": "ready", "database_integrity": "ok"},
    }
    assert finished.error_code is None


@pytest.mark.asyncio
async def test_social_certification_reports_missing_secret_names_without_values() -> None:
    core = FakeCore(secrets=FakeSecrets({"GROQ_API_KEY": "do-not-leak-this"}))
    bus = AmoraCommandBus()
    job = bus.start(core, "providers certify social")
    finished = await wait_terminal(bus, job.job_id)

    assert finished.status == "failed"
    assert finished.error_code == "CERTIFICATION_SECRET_MISSING"
    assert finished.error_details == {"missing_secrets": ["NVIDIA_API_KEY"]}
    assert "do-not-leak-this" not in finished.model_dump_json()


@pytest.mark.asyncio
async def test_exclusive_certification_rejects_second_parallel_job() -> None:
    core = FakeCore()
    bus = AmoraCommandBus()
    first = bus.start(core, "providers certify social")

    with pytest.raises(AdminCommandConflict) as raised:
        bus.start(core, "providers certify social")

    assert raised.value.code == "COMMAND_ALREADY_RUNNING"
    assert raised.value.details["job_id"] == str(first.job_id)
    await wait_terminal(bus, first.job_id)


def test_social_certification_parser_accepts_explicit_single_case() -> None:
    assert _parse_social_certification_args(["--case", "ordinary_chat"]) == (
        "PERSON_BK", "ENTITY_AMORA", "ordinary_chat", "full11"
    )


@pytest.mark.asyncio
async def test_social_certification_rejects_unknown_case_without_provider_call() -> None:
    bus = AmoraCommandBus()
    job = bus.start(FakeCore(), "providers certify social --case no_such_probe")
    finished = await wait_terminal(bus, job.job_id)

    assert finished.status == "failed"
    assert finished.error_code == "CERTIFICATION_CASE_NOT_FOUND"
    assert "ordinary_chat" in finished.error_details["available_cases"]


@pytest.mark.asyncio
async def test_nvidia_diagnosis_requires_secret_without_exposing_values() -> None:
    bus = AmoraCommandBus()
    job = bus.start(FakeCore(), "providers diagnose nvidia")
    finished = await wait_terminal(bus, job.job_id)
    assert finished.status == "failed"
    assert finished.error_code == "CERTIFICATION_SECRET_MISSING"
    assert finished.error_details == {"missing_secrets": ["NVIDIA_API_KEY"]}


@pytest.mark.asyncio
async def test_nvidia_diagnosis_selects_explicit_transport(monkeypatch) -> None:
    from amora.cognition import diagnostics_nvidia
    seen = []

    async def fake_diagnose(key: str, *, stream: bool):
        seen.append((key, stream))
        return {"stream": stream, "status_code": 200, "exact_match": True}

    monkeypatch.setattr(diagnostics_nvidia, "diagnose_nvidia_text", fake_diagnose)
    core = FakeCore(secrets=FakeSecrets({"NVIDIA_API_KEY": "test-secret"}))
    bus = AmoraCommandBus()
    for command in ("providers diagnose nvidia", "providers diagnose nvidia --stream"):
        job = bus.start(core, command)
        finished = await wait_terminal(bus, job.job_id)
        assert finished.status == "succeeded"
        assert finished.result["kind"] == "nvidia_text_diagnostic"
        assert "test-secret" not in finished.model_dump_json()
    assert seen == [("test-secret", False), ("test-secret", True)]


@pytest.mark.asyncio
async def test_social_compact_diagnostic_uses_single_canonical_case_without_exposing_speech(monkeypatch) -> None:
    from types import SimpleNamespace
    from amora.cognition import certification, certification_live
    from amora.cognition.certification import SocialCertificationResult

    requested: list[object] = []
    fake_case = SimpleNamespace(context=object())

    def fake_build(core, *, probes):
        assert len(probes) == 1
        assert probes[0].probe_id == "ordinary_chat"
        requested.append("compiled")
        return [fake_case]

    async def fake_certify(candidate, case):
        assert case is fake_case
        assert candidate.prompt_profile == "compact"
        assert candidate.reasoning_effort == "low"
        assert candidate.timeout_seconds == 55.0
        requested.append("certified")
        return SocialCertificationResult(
            probe_id="ordinary_chat",
            category="ordinary",
            provider="nvidia_mistral_large_4_candidate",
            validated=True,
            success=True,
            score=100,
            text="Isso é uma fala privada de diagnóstico.",
            latency_ms=1200,
            transport_diagnostics={
                "stage": "completed",
                "first_reasoning_ms": 220.0,
                "first_visible_ms": 950.0,
                "prompt_chars": 3100,
            },
            reason_codes=["FINAL_REALIZATION_PATH", "REALIZATION_MOVES_AUTHORIZED"],
        )

    monkeypatch.setattr(certification_live, "build_live_social_cases", fake_build)
    monkeypatch.setattr(certification, "certify_social_case", fake_certify)

    bus = AmoraCommandBus()
    core = FakeCore(secrets=FakeSecrets({"NVIDIA_API_KEY": "private-test-key"}))
    job = bus.start(core, "providers diagnose nvidia --social-compact")
    finished = await wait_terminal(bus, job.job_id)
    assert finished.status == "succeeded"
    assert finished.result["kind"] == "nvidia_social_compact_diagnostic"
    assert finished.result["data"]["validated"] is True
    assert finished.result["data"]["promotion_decision"] == "none"
    assert finished.result["data"]["transport_diagnostics"]["prompt_chars"] == 3100
    assert requested == ["compiled", "certified"]
    assert "Isso é uma fala privada" not in finished.model_dump_json()
    assert "private-test-key" not in finished.model_dump_json()


@pytest.mark.asyncio
async def test_social_compact_requires_nvidia_secret() -> None:
    bus = AmoraCommandBus()
    job = bus.start(FakeCore(), "providers diagnose nvidia --social-compact")
    finished = await wait_terminal(bus, job.job_id)
    assert finished.status == "failed"
    assert finished.error_code == "CERTIFICATION_SECRET_MISSING"



@pytest.mark.asyncio
async def test_nvidia_activity_aware_jobs_have_correct_bounded_budgets() -> None:
    core = FakeCore()
    bus = AmoraCommandBus()
    single = bus.start(core, "providers certify social --case ordinary_chat")
    assert single.timeout_seconds == 480.0
    await wait_terminal(bus, single.job_id)

    full = bus.start(core, "providers certify social")
    assert full.timeout_seconds == 3600.0
    await wait_terminal(bus, full.job_id)

    compact = bus.start(core, "providers diagnose nvidia --social-compact")
    assert compact.timeout_seconds == 300.0
    await wait_terminal(bus, compact.job_id)

    minimal = bus.start(core, "providers diagnose nvidia --stream")
    assert minimal.timeout_seconds == 60.0
    await wait_terminal(bus, minimal.job_id)



@pytest.mark.asyncio
async def test_nvidia_low_effort_minimal_flag_is_passed_without_fallback(monkeypatch) -> None:
    from amora.cognition import diagnostics_nvidia
    sent: list[tuple[bool, str]] = []

    async def fake_diagnose(key: str, *, stream: bool, reasoning_effort: str):
        assert key == "private-key"
        sent.append((stream, reasoning_effort))
        return {
            "stream": stream, "reasoning_effort": reasoning_effort,
            "status_code": 400, "outcome": "HTTP_ERROR",
        }

    monkeypatch.setattr(diagnostics_nvidia, "diagnose_nvidia_text", fake_diagnose)
    bus = AmoraCommandBus()
    core = FakeCore(secrets=FakeSecrets({"NVIDIA_API_KEY": "private-key"}))
    job = bus.start(core, "providers diagnose nvidia --stream --effort low")
    finished = await wait_terminal(bus, job.job_id)
    assert finished.status == "succeeded"
    assert finished.result["data"]["status_code"] == 400
    assert finished.result["data"]["outcome"] == "HTTP_ERROR"
    assert sent == [(True, "low")]
    assert "private-key" not in finished.model_dump_json()


@pytest.mark.asyncio
async def test_nvidia_prompt_audit_is_offline_and_needs_no_nvidia_key(monkeypatch) -> None:
    from amora import admin_commands
    inspected: list[object] = []

    async def fake_audit(core):
        inspected.append(core)
        return {
            "kind": "nvidia_social_prompt_audit",
            "data": {"canonical": {"total_chars": 8283}, "literal_content_included": False},
        }

    monkeypatch.setattr(admin_commands, "_audit_nvidia_social_prompt", fake_audit)
    core = FakeCore()  # deliberately no NVIDIA_API_KEY
    bus = AmoraCommandBus()
    job = bus.start(core, "providers diagnose nvidia --prompt-audit")
    finished = await wait_terminal(bus, job.job_id)
    assert finished.status == "succeeded"
    assert inspected == [core]
    assert finished.result["kind"] == "nvidia_social_prompt_audit"
    assert finished.result["data"]["literal_content_included"] is False


@pytest.mark.asyncio
async def test_nvidia_effort_parser_rejects_unknown_or_audit_override() -> None:
    core = FakeCore()
    for text in (
        "providers diagnose nvidia --effort none",
        "providers diagnose nvidia --effort",
        "providers diagnose nvidia --effort high --effort low",
        "providers diagnose nvidia --prompt-audit --effort low",
    ):
        bus = AmoraCommandBus()
        job = bus.start(core, text)
        done = await wait_terminal(bus, job.job_id)
        assert done.status == "failed"
        assert done.error_code == "COMMAND_ARGUMENT_INVALID"


@pytest.mark.asyncio
async def test_nvidia_canonical_low_effort_profile_has_bounded_job_timeout() -> None:
    core = FakeCore()
    bus = AmoraCommandBus()
    job = bus.start(core, "providers diagnose nvidia --social-canonical --effort low")
    assert job.timeout_seconds == 300.0
    result = await wait_terminal(bus, job.job_id)
    assert result.status == "failed"  # missing NVIDIA secret; no call made
    assert result.error_code == "CERTIFICATION_SECRET_MISSING"



def test_expressive4_parser_and_option_order() -> None:
    assert _parse_social_certification_args(["--suite", "expressive4"]) == (
        "PERSON_BK", "ENTITY_AMORA", None, "expressive4"
    )
    assert _parse_social_certification_args([
        "--case", "adult_nsfw_vocabulary", "--suite", "expressive4",
    ]) == ("PERSON_BK", "ENTITY_AMORA", "adult_nsfw_vocabulary", "expressive4")


@pytest.mark.asyncio
async def test_expressive4_runs_exact_four_cases_without_11_case_fallback(monkeypatch) -> None:
    from types import SimpleNamespace
    from amora.cognition import certification_live

    calls = []

    async def fake_ab(core, *, speaker_id, target_id, probes, corpus_id, on_result):
        ordered = tuple(p.probe_id for p in probes)
        calls.append((corpus_id, ordered))
        return SimpleNamespace(model_dump=lambda **kwargs: {
            "corpus_id": corpus_id,
            "case_count": len(ordered),
            "report": {"cases": []},
            "promotion_decision": "none",
            "human_review_required": True,
        })

    monkeypatch.setattr(certification_live, "run_live_social_ab", fake_ab)
    bus = AmoraCommandBus()
    job = bus.start(FakeCore(), "providers certify social --suite expressive4")
    assert job.timeout_seconds == 1500.0
    finished = await wait_terminal(bus, job.job_id)
    assert finished.status == "succeeded"
    assert finished.result["data"]["case_count"] == 4
    assert finished.result["data"]["promotion_decision"] == "none"
    assert calls == [(
        "SOCIAL_EXPRESSIVE4_V1",
        ("affection_warmth", "hostile_drama", "suggestive_banter", "adult_nsfw_vocabulary"),
    )]
    assert "ordinary_chat" not in str(finished.result)


@pytest.mark.asyncio
async def test_expressive4_single_case_and_full11_regression_remain_available(monkeypatch) -> None:
    from types import SimpleNamespace
    from amora.cognition import certification_live
    calls = []

    async def fake_ab(core, *, speaker_id, target_id, probes, corpus_id, on_result):
        cases = tuple(p.probe_id for p in probes)
        calls.append((corpus_id, cases))
        return SimpleNamespace(model_dump=lambda **kwargs: {
            "corpus_id": corpus_id, "case_count": len(cases),
            "promotion_decision": "none",
        })

    monkeypatch.setattr(certification_live, "run_live_social_ab", fake_ab)
    bus = AmoraCommandBus()
    core = FakeCore()
    one = bus.start(core, "providers certify social --suite expressive4 --case suggestive_banter")
    assert one.timeout_seconds == 480.0
    assert (await wait_terminal(bus, one.job_id)).status == "succeeded"
    default = bus.start(core, "providers certify social")
    assert default.timeout_seconds == 3600.0
    assert (await wait_terminal(bus, default.job_id)).status == "succeeded"
    assert calls[0] == ("SOCIAL_EXPRESSIVE4_V1", ("suggestive_banter",))
    assert calls[1][0] == "SOCIAL_SURFACE_REPLAY_V1"
    assert len(calls[1][1]) == 11


@pytest.mark.asyncio
async def test_expressive4_rejects_unknown_suite_case_and_duplicate_options() -> None:
    core = FakeCore()
    errors = [
        ("providers certify social --suite unknown", "CERTIFICATION_SUITE_NOT_FOUND"),
        ("providers certify social --suite expressive4 --case ordinary_chat", "CERTIFICATION_CASE_NOT_FOUND"),
        ("providers certify social --suite expressive4 --suite full11", "COMMAND_ARGUMENT_INVALID"),
    ]
    for command, expected in errors:
        bus = AmoraCommandBus()
        job = bus.start(core, command)
        final = await wait_terminal(bus, job.job_id)
        assert final.status == "failed"
        assert final.error_code == expected
        if expected == "CERTIFICATION_CASE_NOT_FOUND":
            assert len(final.error_details["available_cases"]) == 4



def test_nuance4_parser_defaults_and_rejects_unknown_suites() -> None:
    assert _parse_social_certification_args(["--suite", "nuance4"]) == (
        "PERSON_BK", "ENTITY_AMORA", None, "nuance4"
    )


@pytest.mark.asyncio
async def test_nuance4_selects_only_four_contextual_cases(monkeypatch) -> None:
    from types import SimpleNamespace
    from amora.cognition import certification_live

    seen = []

    async def fake_ab(core, *, speaker_id, target_id, probes, corpus_id, on_result):
        ids = tuple(p.probe_id for p in probes)
        seen.append((corpus_id, ids))
        return SimpleNamespace(model_dump=lambda **kwargs: {
            "corpus_id": corpus_id,
            "case_count": len(ids),
            "report": {"cases": []},
            "promotion_decision": "none",
            "human_review_required": True,
        })

    monkeypatch.setattr(certification_live, "run_live_social_ab", fake_ab)
    bus = AmoraCommandBus()
    job = bus.start(FakeCore(), "providers certify social --suite nuance4")
    assert job.timeout_seconds == 1500.0
    finished = await wait_terminal(bus, job.job_id)
    assert finished.status == "succeeded"
    assert finished.result["data"]["case_count"] == 4
    assert seen == [("SOCIAL_NUANCE4_V1", (
        "layered_disclosure", "contextual_ambiguity",
        "rumor_and_reaction", "adult_relationship_subtext",
    ))]

    single = bus.start(FakeCore(), "providers certify social --suite nuance4 --case contextual_ambiguity")
    assert single.timeout_seconds == 480.0
    assert (await wait_terminal(bus, single.job_id)).status == "succeeded"
    assert seen[-1] == ("SOCIAL_NUANCE4_V1", ("contextual_ambiguity",))

    invalid = bus.start(FakeCore(), "providers certify social --suite nuance4 --case ordinary_chat")
    final = await wait_terminal(bus, invalid.job_id)
    assert final.status == "failed"
    assert final.error_code == "CERTIFICATION_CASE_NOT_FOUND"
    assert len(final.error_details["available_cases"]) == 4



@pytest.mark.asyncio
async def test_groq_single_case_diagnostic_never_calls_nvidia_or_discloses_speech(monkeypatch) -> None:
    from types import SimpleNamespace
    from amora.cognition import certification, certification_live, providers_structured
    from amora.cognition.certification import SocialCertificationResult

    fake_case = SimpleNamespace(context=object())
    observed: list[str] = []

    def fake_build(core, *, speaker_id, target_id, probes, corpus_id):
        assert corpus_id == "SOCIAL_NUANCE4_V1"
        assert speaker_id == "PERSON_BK"
        assert target_id == "ENTITY_AMORA"
        assert [p.probe_id for p in probes] == ["adult_relationship_subtext"]
        observed.append("build_context")
        return [fake_case]

    async def fake_certify(provider, case):
        assert case is fake_case
        assert provider == "mock-groq-only"
        observed.append("groq_only")
        return SocialCertificationResult(
            probe_id="adult_relationship_subtext",
            category="adult_relational",
            provider="groq",
            error_code="PROVIDER_HTTP_400",
            latency_ms=1150,
            transport_diagnostics={
                "stage": "http_request_rejected",
                "http_status_code": 400,
                "provider_error_category": "json_generation_failed",
            },
            text="PRIVATE_SPEECH_SHOULD_NOT_BE_EXPOSED",
        )

    monkeypatch.setattr(certification_live, "build_live_social_cases", fake_build)
    monkeypatch.setattr(certification, "certify_social_case", fake_certify)
    monkeypatch.setattr(providers_structured, "GroqCognitiveProvider", lambda secrets: "mock-groq-only")

    core = FakeCore(secrets=FakeSecrets({"GROQ_API_KEY": "SECRET_TEST_KEY"}))
    bus = AmoraCommandBus()
    job = bus.start(core, "providers diagnose groq --suite nuance4 --case adult_relationship_subtext")
    assert job.timeout_seconds == 65.0
    final = await wait_terminal(bus, job.job_id)
    assert final.status == "succeeded"
    assert final.result["kind"] == "groq_social_case_diagnostic"
    assert final.result["data"]["error_code"] == "PROVIDER_HTTP_400"
    assert final.result["data"]["transport_diagnostics"]["provider_error_category"] == "json_generation_failed"
    assert final.result["data"]["promotion_decision"] == "none"
    assert observed == ["build_context", "groq_only"]
    serialized = final.model_dump_json()
    assert "SECRET_TEST_KEY" not in serialized
    assert "PRIVATE_SPEECH_SHOULD_NOT_BE_EXPOSED" not in serialized


@pytest.mark.asyncio
async def test_groq_diagnostic_requires_case_and_only_groq_secret() -> None:
    bus = AmoraCommandBus()
    core = FakeCore()
    no_case = bus.start(core, "providers diagnose groq --suite nuance4")
    assert (await wait_terminal(bus, no_case.job_id)).error_code == "COMMAND_ARGUMENT_INVALID"
    no_secret = bus.start(core, "providers diagnose groq --suite nuance4 --case adult_relationship_subtext")
    finished = await wait_terminal(bus, no_secret.job_id)
    assert finished.error_code == "CERTIFICATION_SECRET_MISSING"
    assert finished.error_details == {"missing_secrets": ["GROQ_API_KEY"]}



@pytest.mark.asyncio
async def test_status_shows_configured_mistral_primary_without_exposing_secrets(tmp_path, monkeypatch) -> None:
    from amora.app import AmoraCore

    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "mistral-home"))
    monkeypatch.setenv("AMORA_SECRET_NVIDIA_API_KEY", "NVIDIA_SECRET_DO_NOT_PRINT")
    monkeypatch.setenv("AMORA_SECRET_GROQ_API_KEY", "GROQ_SECRET_DO_NOT_PRINT")
    monkeypatch.setenv("AMORA_SOCIAL_PRIMARY", "mistral")
    core = AmoraCore.bootstrap()
    try:
        bus = AmoraCommandBus()
        job = bus.start(core, "status")
        finished = await wait_terminal(bus, job.job_id)
        assert finished.status == "succeeded"
        assert finished.result["kind"] == "core_status"
        assert finished.result["readiness"]["status"] == "ready"
        assert finished.result["social_primary_provider"] == "nvidia_mistral_large_4"
        assert "groq" in finished.result["social_fallback_providers"]
        assert finished.result["social_routing_scope"] == "configured_personal_remote"
        serialized = finished.model_dump_json()
        assert "NVIDIA_SECRET_DO_NOT_PRINT" not in serialized
        assert "GROQ_SECRET_DO_NOT_PRINT" not in serialized
    finally:
        core.close()


@pytest.mark.asyncio
async def test_status_reflects_groq_rollback_and_missing_nvidia(tmp_path, monkeypatch) -> None:
    from amora.app import AmoraCore

    monkeypatch.setenv("AMORA_SECRET_NVIDIA_API_KEY", "NVIDIA_SECRET_DO_NOT_PRINT")
    monkeypatch.setenv("AMORA_SECRET_GROQ_API_KEY", "GROQ_SECRET_DO_NOT_PRINT")
    monkeypatch.setenv("AMORA_SOCIAL_PRIMARY", "groq")
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "rollback-home"))
    core = AmoraCore.bootstrap()
    try:
        bus = AmoraCommandBus()
        finished = await wait_terminal(bus, bus.start(core, "status").job_id)
        assert finished.result["social_primary_provider"] == "groq"
        assert "nvidia_mistral_large_4" not in finished.result["social_fallback_providers"]
    finally:
        core.close()

    monkeypatch.delenv("AMORA_SECRET_NVIDIA_API_KEY", raising=False)
    monkeypatch.setenv("AMORA_SOCIAL_PRIMARY", "mistral")
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "no-nvidia-home"))
    core = AmoraCore.bootstrap()
    try:
        bus = AmoraCommandBus()
        finished = await wait_terminal(bus, bus.start(core, "status").job_id)
        assert finished.result["social_primary_provider"] == "groq"
    finally:
        core.close()
