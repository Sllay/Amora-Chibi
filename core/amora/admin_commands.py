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


def _parse_social_certification_args(args: list[str]) -> tuple[str, str, str | None]:
    speaker_id = "PERSON_BK"
    target_id = "ENTITY_AMORA"
    probe_id: str | None = None
    index = 0
    while index < len(args):
        option = args[index]
        if option not in {"--speaker-id", "--target-id", "--case"}:
            raise AdminCommandError(
                "COMMAND_ARGUMENT_INVALID",
                f"argumento não suportado: {option}",
                details={"usage": "providers certify social [--case ordinary_chat] [--speaker-id PERSON_BK] [--target-id ENTITY_AMORA]"},
            )
        if index + 1 >= len(args):
            raise AdminCommandError("COMMAND_ARGUMENT_INVALID", f"valor ausente para {option}")
        value = args[index + 1].strip()
        if not value or len(value) > 128:
            raise AdminCommandError("COMMAND_ARGUMENT_INVALID", f"valor inválido para {option}")
        if option == "--speaker-id":
            speaker_id = value
        elif option == "--target-id":
            target_id = value
        else:
            probe_id = value
        index += 2
    return speaker_id, target_id, probe_id


async def _command_providers_certify_social(core: Any, args: list[str]) -> dict[str, Any]:
    speaker_id, target_id, probe_id = _parse_social_certification_args(args)

    from amora.cognition.certification import SOCIAL_SURFACE_REPLAY_V1
    from amora.cognition.certification_live import (
        LiveCertificationConfigurationError,
        run_live_social_ab,
    )

    if probe_id is not None and not any(p.probe_id == probe_id for p in SOCIAL_SURFACE_REPLAY_V1):
        raise AdminCommandError(
            "CERTIFICATION_CASE_NOT_FOUND", "caso de certificação inexistente",
            details={"available_cases": [p.probe_id for p in SOCIAL_SURFACE_REPLAY_V1]},
        )
    probes = (
        tuple(p for p in SOCIAL_SURFACE_REPLAY_V1 if p.probe_id == probe_id)
        if probe_id is not None else SOCIAL_SURFACE_REPLAY_V1
    )

    try:
        report = await run_live_social_ab(
            core,
            speaker_id=speaker_id,
            target_id=target_id,
            probes=probes,
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


async def _diagnose_nvidia_social_compact(core: Any) -> dict[str, Any]:
    """One real Core snapshot, one NVIDIA call; no Groq, state write, or promotion."""
    from amora.cognition.certification import (
        SOCIAL_SURFACE_REPLAY_V1,
        certify_social_case,
    )
    from amora.cognition.certification_live import build_live_social_cases
    from amora.cognition.providers_nvidia import NvidiaMistralLarge4Provider

    ordinary = next(p for p in SOCIAL_SURFACE_REPLAY_V1 if p.probe_id == "ordinary_chat")
    case = build_live_social_cases(core, probes=(ordinary,))[0]
    candidate = NvidiaMistralLarge4Provider(core.secrets, prompt_profile="compact")
    result = await certify_social_case(candidate, case)
    # Discard generated speech and mechanical grade: this is transport/profile
    # diagnosis, not a comparable certification result or user conversation.
    return {
        "kind": "nvidia_social_compact_diagnostic",
        "summary": "Diagnóstico social compacto isolado; não promove provider nem altera estado.",
        "data": {
            "profile": "compact",
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


async def _command_providers_diagnose_nvidia(core: Any, args: list[str]) -> dict[str, Any]:
    if args not in ([], ["--stream"], ["--no-stream"], ["--social-compact"]):
        raise AdminCommandError(
            "COMMAND_ARGUMENT_INVALID",
            "uso: providers diagnose nvidia [--stream | --no-stream | --social-compact]",
        )
    from amora.cognition.diagnostics_nvidia import diagnose_nvidia_text

    api_key = core.secrets.get("NVIDIA_API_KEY")
    if not api_key:
        raise AdminCommandError(
            "CERTIFICATION_SECRET_MISSING",
            "NVIDIA_API_KEY não configurada",
            details={"missing_secrets": ["NVIDIA_API_KEY"]},
        )
    if args == ["--social-compact"]:
        return await _diagnose_nvidia_social_compact(core)
    stream = args == ["--stream"]
    result = await diagnose_nvidia_text(api_key, stream=stream)
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
        usage="providers diagnose nvidia [--stream | --no-stream | --social-compact]",
        description="Diagnóstico mínimo NVIDIA ou social compacto isolado, sem promoção.",
        timeout_seconds=60.0,
        handler=_command_providers_diagnose_nvidia,
        exclusive=True,
    ),
    RegisteredCommand(
        path=("providers", "certify", "social"),
        usage="providers certify social [--case ordinary_chat] [--speaker-id PERSON_BK] [--target-id ENTITY_AMORA]",
        description="Executa A/B live Qwen × Mistral read-only (ou um único caso com --case).",
        timeout_seconds=300.0,
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
            timeout_seconds=command.timeout_seconds,
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
            job.result = await asyncio.wait_for(command.handler(core, args), timeout=command.timeout_seconds)
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
