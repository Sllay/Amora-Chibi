from __future__ import annotations

import asyncio
import shlex
from contextvars import ContextVar
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Literal
from uuid import UUID, uuid4

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field


JobStatus = Literal["queued", "running", "succeeded", "failed", "timed_out"]
CommandHandler = Callable[[Any, list[str]], Awaitable[dict[str, Any]]]
_progress_sink: ContextVar[Callable[[dict[str, Any]], None] | None] = ContextVar("amora_command_progress", default=None)


class AdminCommandRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    command: str = Field(min_length=1, max_length=500)


class AdminCommandJob(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["amora.admin-command-job.v1"] = "amora.admin-command-job.v1"
    job_id: UUID
    command: str
    command_path: str
    permission: Literal["operator"] = "operator"
    timeout_seconds: float = Field(gt=0.0)
    status: JobStatus
    created_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None
    progress: list[dict[str, Any]] = Field(default_factory=list)
    error_code: str | None = None
    error_type: str | None = None
    error_details: dict[str, Any] = Field(default_factory=dict)


class AdminCommandCatalogItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str
    usage: str
    description: str
    permission: Literal["operator"] = "operator"
    timeout_seconds: float
    exclusive: bool = False


class AdminCommandError(RuntimeError):
    def __init__(self, code: str, detail: str, *, details: dict[str, Any] | None = None) -> None:
        self.code = code
        self.detail = detail
        self.details = details or {}
        super().__init__(detail)


class AdminCommandConflict(AdminCommandError):
    pass


@dataclass(frozen=True, slots=True)
class RegisteredCommand:
    path: tuple[str, ...]
    usage: str
    description: str
    timeout_seconds: float
    handler: CommandHandler
    exclusive: bool = False

    @property
    def path_text(self) -> str:
        return " ".join(self.path)


async def _command_help(core: Any, args: list[str]) -> dict[str, Any]:
    del core
    if args:
        raise AdminCommandError("COMMAND_ARGUMENT_INVALID", "help não aceita argumentos")
    return {
        "kind": "command_help",
        "summary": "Comandos administrativos disponíveis.",
        "commands": [item.model_dump(mode="json") for item in command_catalog()],
    }


async def _command_status(core: Any, args: list[str]) -> dict[str, Any]:
    if args:
        raise AdminCommandError("COMMAND_ARGUMENT_INVALID", "status não aceita argumentos")
    readiness = core.readiness()
    return {
        "kind": "core_status",
        "summary": f"Core: {readiness.get('status', 'unknown')}",
        "readiness": readiness,
    }


def _parse_social_certification_args(args: list[str]) -> tuple[str, str, str | None, str]:
    """Parse only allowed certification selectors; never accept arbitrary prompts."""
    speaker_id = "PERSON_BK"
    target_id = "ENTITY_AMORA"
    probe_id: str | None = None
    suite = "full11"
    seen: set[str] = set()
    index = 0
    while index < len(args):
        option = args[index]
        if option not in {"--speaker-id", "--target-id", "--case", "--suite"}:
            raise AdminCommandError(
                "COMMAND_ARGUMENT_INVALID",
                f"argumento não suportado: {option}",
                details={
                    "usage": "providers certify social [--suite nuance4|expressive4|full11] "
                    "[--case affection_warmth] [--speaker-id PERSON_BK] "
                    "[--target-id ENTITY_AMORA]"
                },
            )
        if option in seen:
            raise AdminCommandError("COMMAND_ARGUMENT_INVALID", f"argumento duplicado: {option}")
        seen.add(option)
        if index + 1 >= len(args):
            raise AdminCommandError("COMMAND_ARGUMENT_INVALID", f"valor ausente para {option}")
        value = args[index + 1].strip()
        if not value or len(value) > 128:
            raise AdminCommandError("COMMAND_ARGUMENT_INVALID", f"valor inválido para {option}")
        if option == "--speaker-id":
            speaker_id = value
        elif option == "--target-id":
            target_id = value
        elif option == "--case":
            probe_id = value
        else:
            suite = value
        index += 2
    if suite not in {"nuance4", "expressive4", "full11"}:
        raise AdminCommandError(
            "CERTIFICATION_SUITE_NOT_FOUND", "bateria de certificação inexistente",
            details={"available_suites": ["nuance4", "expressive4", "full11"]},
        )
    return speaker_id, target_id, probe_id, suite


async def _command_providers_certify_social(core: Any, args: list[str]) -> dict[str, Any]:
    speaker_id, target_id, probe_id, suite = _parse_social_certification_args(args)

    from amora.cognition.certification import (
        SOCIAL_EXPRESSIVE4_V1,
        SOCIAL_NUANCE4_V1,
        SOCIAL_SURFACE_REPLAY_V1,
    )
    from amora.cognition.certification_live import (
        CORPUS_ID,
        EXPRESSIVE_CORPUS_ID,
        NUANCE_CORPUS_ID,
        LiveCertificationConfigurationError,
        run_live_social_ab,
    )

    if suite == "expressive4":
        corpus_id, suite_probes = EXPRESSIVE_CORPUS_ID, SOCIAL_EXPRESSIVE4_V1
    elif suite == "nuance4":
        corpus_id, suite_probes = NUANCE_CORPUS_ID, SOCIAL_NUANCE4_V1
    else:
        corpus_id, suite_probes = CORPUS_ID, SOCIAL_SURFACE_REPLAY_V1
    if probe_id is not None and not any(p.probe_id == probe_id for p in suite_probes):
        raise AdminCommandError(
            "CERTIFICATION_CASE_NOT_FOUND", "caso de certificação inexistente",
            details={"available_cases": [p.probe_id for p in suite_probes]},
        )
    probes = (
        tuple(p for p in suite_probes if p.probe_id == probe_id)
        if probe_id is not None else suite_probes
    )

    try:
        report = await run_live_social_ab(
            core,
            speaker_id=speaker_id,
            target_id=target_id,
            probes=probes,
            corpus_id=corpus_id,
            on_result=lambda item: (_progress_sink.get() or (lambda _: None))(item.model_dump(mode="json")),
        )
    except LiveCertificationConfigurationError as exc:
        raise AdminCommandError(
            "CERTIFICATION_SECRET_MISSING",
            "credenciais de provider obrigatórias não estão configuradas",
            details={"missing_secrets": list(exc.missing_secrets)},
        ) from exc

    return {
        "kind": "social_provider_ab",
        "summary": "A/B social concluído; revisão humana obrigatória; promoção automática desabilitada.",
        "data": report.model_dump(mode="json"),
    }


async def _diagnose_nvidia_social_profile(
    core: Any, *, profile: str, reasoning_effort: str,
) -> dict[str, Any]:
    """One real Core snapshot + one NVIDIA call; no Groq or state writes."""
    from amora.cognition.certification import SOCIAL_SURFACE_REPLAY_V1, certify_social_case
    from amora.cognition.certification_live import build_live_social_cases
    from amora.cognition.providers_nvidia import NvidiaMistralLarge4Provider

    ordinary = next(p for p in SOCIAL_SURFACE_REPLAY_V1 if p.probe_id == "ordinary_chat")
    case = build_live_social_cases(core, probes=(ordinary,))[0]
    candidate = NvidiaMistralLarge4Provider(
        core.secrets, prompt_profile=profile, reasoning_effort=reasoning_effort
    )
    result = await certify_social_case(candidate, case)
    # Do not expose speech, private reasoning or mechanical grade in a diagnostic.
    return {
        "kind": f"nvidia_social_{profile}_diagnostic",
        "summary": "Sonda social isolada; sem alteração de estado ou promoção.",
        "data": {
            "profile": profile,
            "reasoning_effort": reasoning_effort,
            "probe_id": ordinary.probe_id,
            "validated": result.validated,
            "error_code": result.error_code,
            "latency_ms": result.latency_ms,
            "transport_diagnostics": result.transport_diagnostics,
            "reason_codes": [
                code for code in result.reason_codes
                if code.startswith(("PROVIDER_", "NVIDIA_", "REALIZATION_"))
            ],
            "promotion_decision": "none",
            "human_review_required": True,
        },
    }


async def _audit_nvidia_social_prompt(core: Any) -> dict[str, Any]:
    """Compile the same Core case locally, return no literal conversational data."""
    from amora.cognition.certification import SOCIAL_SURFACE_REPLAY_V1
    from amora.cognition.certification_live import build_live_social_cases
    from amora.cognition.providers_nvidia import audit_nvidia_social_prompt

    ordinary = next(p for p in SOCIAL_SURFACE_REPLAY_V1 if p.probe_id == "ordinary_chat")
    case = build_live_social_cases(core, probes=(ordinary,))[0]
    return {
        "kind": "nvidia_social_prompt_audit",
        "summary": "Auditoria local de tamanho por seção, sem rede ou texto privado.",
        "data": {
            "probe_id": ordinary.probe_id,
            **audit_nvidia_social_prompt(case.context),
        },
    }


async def _command_providers_diagnose_nvidia(core: Any, args: list[str]) -> dict[str, Any]:
    from amora.cognition.diagnostics_nvidia import diagnose_nvidia_text

    # Keep parser intentionally closed: no arbitrary endpoint, model or prompt.
    remaining = list(args)
    effort: str | None = None
    if "--effort" in remaining:
        if remaining.count("--effort") != 1:
            raise AdminCommandError("COMMAND_ARGUMENT_INVALID", "--effort repetido")
        i = remaining.index("--effort")
        if i + 1 >= len(remaining) or remaining[i + 1] not in {"low", "medium", "high"}:
            raise AdminCommandError(
                "COMMAND_ARGUMENT_INVALID", "--effort deve ser low, medium ou high"
            )
        effort = remaining[i + 1]
        remaining = remaining[:i] + remaining[i + 2:]

    allowed = (
        [], ["--stream"], ["--no-stream"], ["--social-compact"],
        ["--social-canonical"], ["--prompt-audit"],
    )
    if remaining not in allowed or (remaining == ["--prompt-audit"] and effort is not None):
        raise AdminCommandError(
            "COMMAND_ARGUMENT_INVALID",
            "uso: providers diagnose nvidia [--stream | --no-stream | --social-compact | --social-canonical | --prompt-audit] [--effort low|medium|high]",
        )

    if remaining == ["--prompt-audit"]:
        # No API credential or remote call needed for a read-only size audit.
        return await _audit_nvidia_social_prompt(core)

    api_key = core.secrets.get("NVIDIA_API_KEY")
    if not api_key:
        raise AdminCommandError(
            "CERTIFICATION_SECRET_MISSING",
            "NVIDIA_API_KEY não configurada",
            details={"missing_secrets": ["NVIDIA_API_KEY"]},
        )

    if remaining in (["--social-compact"], ["--social-canonical"]):
        return await _diagnose_nvidia_social_profile(
            core,
            profile="compact" if remaining == ["--social-compact"] else "canonical",
            reasoning_effort=effort or "low",
        )

    stream = remaining == ["--stream"]
    if effort is None:
        # Preserve the historical minimal benchmark (high) unless explicitly set.
        result = await diagnose_nvidia_text(api_key, stream=stream)
    else:
        result = await diagnose_nvidia_text(api_key, stream=stream, reasoning_effort=effort)
    return {
        "kind": "nvidia_text_diagnostic",
        "summary": "Diagnóstico NVIDIA concluído (sem alterar certificação ou Core).",
        "data": result,
    }


_COMMANDS: tuple[RegisteredCommand, ...] = (
    RegisteredCommand(
        path=("help",),
        usage="help",
        description="Lista comandos administrativos autorizados.",
        timeout_seconds=5.0,
        handler=_command_help,
    ),
    RegisteredCommand(
        path=("status",),
        usage="status",
        description="Mostra readiness do Core sem abrir shell do host.",
        timeout_seconds=5.0,
        handler=_command_status,
    ),
    RegisteredCommand(
        path=("providers", "diagnose", "nvidia"),
        usage="providers diagnose nvidia [--stream | --no-stream | --social-compact | --social-canonical | --prompt-audit] [--effort low|medium|high]",
        description="Teste de esforço NVIDIA ou auditoria local do prompt; sem promoção.",
        timeout_seconds=60.0,
        handler=_command_providers_diagnose_nvidia,
        exclusive=True,
    ),
    RegisteredCommand(
        path=("providers", "certify", "social"),
        usage="providers certify social [--suite nuance4|expressive4|full11] [--case CASE_ID] [--speaker-id PERSON_BK] [--target-id ENTITY_AMORA]",
        description="A/B Qwen × Mistral: 4 cenários de nuance, 4 expressivos ou 11 originais; read-only.",
        timeout_seconds=480.0,
        handler=_command_providers_certify_social,
        exclusive=True,
    ),
)


def command_catalog() -> list[AdminCommandCatalogItem]:
    return [
        AdminCommandCatalogItem(
            path=command.path_text,
            usage=command.usage,
            description=command.description,
            timeout_seconds=command.timeout_seconds,
            exclusive=command.exclusive,
        )
        for command in _COMMANDS
    ]


def _resolve_command(raw_command: str) -> tuple[RegisteredCommand, list[str]]:
    try:
        tokens = shlex.split(raw_command, posix=True)
    except ValueError as exc:
        raise AdminCommandError("COMMAND_PARSE_INVALID", "comando inválido") from exc
    if not tokens:
        raise AdminCommandError("COMMAND_EMPTY", "comando vazio")

    normalized = [token.strip().lower() for token in tokens]
    for command in sorted(_COMMANDS, key=lambda item: len(item.path), reverse=True):
        if tuple(normalized[: len(command.path)]) == command.path:
            return command, tokens[len(command.path) :]
    raise AdminCommandError(
        "COMMAND_NOT_FOUND",
        "comando administrativo desconhecido",
        details={"hint": "use help"},
    )


def _now(core: Any) -> datetime:
    clock = getattr(core, "clock", None)
    if clock is not None and callable(getattr(clock, "now", None)):
        value = clock.now()
        if isinstance(value, datetime):
            return value
    return datetime.now(timezone.utc)


class AmoraCommandBus:
    """Authorized command registry for administrative work; never executes arbitrary shell text."""

    def __init__(self, *, max_history: int = 50) -> None:
        self.max_history = max(10, max_history)
        self._jobs: OrderedDict[UUID, AdminCommandJob] = OrderedDict()
        self._tasks: dict[UUID, asyncio.Task[None]] = {}

    def catalog(self) -> list[AdminCommandCatalogItem]:
        return command_catalog()

    def list_jobs(self, *, limit: int = 20) -> list[AdminCommandJob]:
        values = list(self._jobs.values())[-max(1, min(limit, self.max_history)) :]
        return [job.model_copy(deep=True) for job in reversed(values)]

    def get(self, job_id: UUID) -> AdminCommandJob | None:
        job = self._jobs.get(job_id)
        return job.model_copy(deep=True) if job is not None else None

    def start(self, core: Any, raw_command: str) -> AdminCommandJob:
        command, args = _resolve_command(raw_command)
        # Live high-effort SSE may stay productive well beyond 55 seconds.
        # Budget one-case, full suite and compact diagnostics independently;
        # no unbounded or shell-configurable command execution.
        timeout_seconds = command.timeout_seconds
        if command.path == ("providers", "certify", "social") and "--case" not in args:
            # Four 270-second NVIDIA calls plus Groq/429 budget; bounded.
            timeout_seconds = 1500.0 if any(x in args for x in ("nuance4", "expressive4")) else 3600.0
        elif command.path == ("providers", "diagnose", "nvidia") and (
            "--social-compact" in args or "--social-canonical" in args
        ):
            timeout_seconds = 300.0
        if command.exclusive:
            for existing in self._jobs.values():
                if existing.command_path == command.path_text and existing.status in {"queued", "running"}:
                    raise AdminCommandConflict(
                        "COMMAND_ALREADY_RUNNING",
                        f"{command.path_text} já está em execução",
                        details={"job_id": str(existing.job_id)},
                    )

        job = AdminCommandJob(
            job_id=uuid4(),
            command=raw_command.strip(),
            command_path=command.path_text,
            timeout_seconds=timeout_seconds,
            status="queued",
            created_at=_now(core),
        )
        self._jobs[job.job_id] = job
        self._prune_history()
        task = asyncio.create_task(self._run(core, job.job_id, command, args))
        self._tasks[job.job_id] = task
        task.add_done_callback(lambda _task, job_id=job.job_id: self._tasks.pop(job_id, None))
        return job.model_copy(deep=True)

    async def _run(
        self,
        core: Any,
        job_id: UUID,
        command: RegisteredCommand,
        args: list[str],
    ) -> None:
        job = self._jobs[job_id]
        job.status = "running"
        job.started_at = _now(core)
        def publish(item: dict[str, Any]) -> None:
            # Retain validated partial outcomes only, never raw provider chunks.
            if len(job.progress) < 22:
                job.progress.append(item)

        progress_token = _progress_sink.set(publish)
        try:
            job.result = await asyncio.wait_for(command.handler(core, args), timeout=job.timeout_seconds)
        except asyncio.TimeoutError:
            job.status = "timed_out"
            job.error_code = "COMMAND_TIMEOUT"
            job.error_type = "TimeoutError"
        except AdminCommandError as exc:
            job.status = "failed"
            job.error_code = exc.code
            job.error_type = type(exc).__name__
            job.error_details = exc.details
        except Exception as exc:  # pragma: no cover - live provider boundary
            job.status = "failed"
            job.error_code = "COMMAND_FAILED"
            job.error_type = type(exc).__name__
            status_code = getattr(exc, "status_code", None)
            if isinstance(status_code, int):
                job.error_details = {"status_code": status_code}
        else:
            job.status = "succeeded"
        finally:
            _progress_sink.reset(progress_token)
            job.finished_at = _now(core)

    def _prune_history(self) -> None:
        if len(self._jobs) <= self.max_history:
            return
        for job_id, job in tuple(self._jobs.items()):
            if len(self._jobs) <= self.max_history:
                break
            if job.status not in {"queued", "running"}:
                self._jobs.pop(job_id, None)


def _require_operator(request: Request) -> None:
    if getattr(request.state, "security_role", None) not in {"operator", "admin"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="operator capability required")


def register_admin_command_routes(app: FastAPI, core: Any, require_auth: Callable[..., None]) -> None:
    auth = [Depends(require_auth)]
    bus = AmoraCommandBus()
    app.state.amora_command_bus = bus

    @app.get("/v1/operator/commands/catalog", dependencies=auth)
    def admin_command_catalog(request: Request) -> list[dict[str, Any]]:
        _require_operator(request)
        return [item.model_dump(mode="json") for item in bus.catalog()]

    @app.get("/v1/operator/commands", dependencies=auth)
    def admin_command_jobs(request: Request) -> list[dict[str, Any]]:
        _require_operator(request)
        return [item.model_dump(mode="json") for item in bus.list_jobs()]

    @app.get("/v1/operator/commands/{job_id}", dependencies=auth)
    def admin_command_job(job_id: UUID, request: Request) -> dict[str, Any]:
        _require_operator(request)
        job = bus.get(job_id)
        if job is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="command job not found")
        return job.model_dump(mode="json")

    @app.post("/v1/operator/commands", dependencies=auth, status_code=status.HTTP_202_ACCEPTED)
    async def admin_command_start(payload: AdminCommandRequest, request: Request) -> dict[str, Any]:
        _require_operator(request)
        try:
            job = bus.start(core, payload.command)
        except AdminCommandConflict as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail={"code": exc.code, **exc.details}) from exc
        except AdminCommandError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail={"code": exc.code, **exc.details}) from exc
        return job.model_dump(mode="json")
