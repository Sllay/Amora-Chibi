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
    assert set(catalog) == {"help", "status", "providers certify social"}
    assert catalog["providers certify social"].exclusive is True
    assert catalog["providers certify social"].permission == "operator"


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
        "PERSON_BK", "ENTITY_AMORA", "ordinary_chat"
    )


@pytest.mark.asyncio
async def test_social_certification_rejects_unknown_case_without_provider_call() -> None:
    bus = AmoraCommandBus()
    job = bus.start(FakeCore(), "providers certify social --case no_such_probe")
    finished = await wait_terminal(bus, job.job_id)

    assert finished.status == "failed"
    assert finished.error_code == "CERTIFICATION_CASE_NOT_FOUND"
    assert "ordinary_chat" in finished.error_details["available_cases"]
