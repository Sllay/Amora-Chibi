from __future__ import annotations

import logging
import os
import shutil
import sys
import time
from collections import deque
from dataclasses import dataclass
from threading import RLock
from typing import TYPE_CHECKING, TextIO

if TYPE_CHECKING:
    from amora.app import AmoraCore


@dataclass(slots=True)
class ModuleStatus:
    module_id: str
    label: str
    state: str
    detail: str = ""


@dataclass(slots=True)
class ConsoleNotice:
    timestamp: float
    source: str
    message: str
    count: int = 1


class _DashboardLogHandler(logging.Handler):
    def __init__(self, dashboard: "ServerConsole") -> None:
        super().__init__(logging.WARNING)
        self.dashboard = dashboard
        self._amora_dashboard = True

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.dashboard.record_error(record.name, record.getMessage())
        except Exception:
            self.handleError(record)


class ServerConsole:
    """Small dependency-free terminal UI for the local Amora server.

    The JSON log remains the forensic source of truth. This view is deliberately
    operational: current activity, module/provider health, plugin health and a
    deduplicated tail of meaningful errors. Polling/access logs never become UI
    rows and therefore cannot flood the terminal.
    """

    REFRESH_SECONDS = 0.65
    ACTIVITY_TTL_SECONDS = 2.5
    MAX_NOTICES = 5

    def __init__(
        self,
        *,
        host: str,
        port: int,
        log_path: str,
        stream: TextIO | None = None,
        interactive: bool | None = None,
    ) -> None:
        self.host = host
        self.port = port
        self.log_path = log_path
        self.stream = stream or sys.stderr
        detected = bool(getattr(self.stream, "isatty", lambda: False)()) and os.getenv("TERM", "") != "dumb"
        self.interactive = detected if interactive is None else interactive
        self._lock = RLock()
        self._modules: dict[str, ModuleStatus] = {}
        self._overrides: dict[str, ModuleStatus] = {}
        self._notices: deque[ConsoleNotice] = deque(maxlen=self.MAX_NOTICES)
        self._activity = "INICIANDO"
        self._activity_detail = "bootstrap do Core"
        self._activity_until = 0.0
        self._last_refresh = 0.0
        self._last_render = ""
        self._started = False
        self._logging_handler: logging.Handler | None = None

    def start(self, core: "AmoraCore") -> None:
        with self._lock:
            self._started = True
        self._install_logging_capture()
        self.refresh(core, force=True)
        self.set_activity("IDLE", "aguardando", ttl_seconds=0.0)

    def close(self) -> None:
        handler = self._logging_handler
        if handler is not None:
            root = logging.getLogger()
            root.removeHandler(handler)
            self._logging_handler = None
        if self.interactive:
            try:
                self.stream.write("\x1b[0m\n")
                self.stream.flush()
            except Exception:
                pass

    def _install_logging_capture(self) -> None:
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, "_amora_console", False) or getattr(handler, "_amora_dashboard", False):
                root.removeHandler(handler)
        handler = _DashboardLogHandler(self)
        root.addHandler(handler)
        self._logging_handler = handler

    def set_activity(self, label: str, detail: str = "", *, ttl_seconds: float | None = None) -> None:
        ttl = self.ACTIVITY_TTL_SECONDS if ttl_seconds is None else max(0.0, ttl_seconds)
        with self._lock:
            self._activity = label.upper().strip() or "IDLE"
            self._activity_detail = self._one_line(detail)
            self._activity_until = time.monotonic() + ttl if ttl > 0 else 0.0
        self.render()

    def set_module(self, module_id: str, label: str, state: str, detail: str = "") -> None:
        row = ModuleStatus(module_id=module_id, label=label, state=state.upper(), detail=self._one_line(detail))
        with self._lock:
            self._overrides[module_id] = row
        self.render()

    def clear_module_override(self, module_id: str) -> None:
        with self._lock:
            self._overrides.pop(module_id, None)
        self.render()

    def record_event(self, source: str, message: str) -> None:
        # Events are intentionally folded into the activity/detail line for now.
        self.set_activity(source, message)

    def record_error(self, source: str, message: str) -> None:
        source = self._one_line(source)[:28] or "runtime"
        message = self._one_line(message)[:180] or "erro sem mensagem"
        with self._lock:
            if self._notices and self._notices[-1].source == source and self._notices[-1].message == message:
                self._notices[-1].count += 1
                self._notices[-1].timestamp = time.time()
            else:
                self._notices.append(ConsoleNotice(time.time(), source, message))
        self.render(force=True)

    def heartbeat(self, core: "AmoraCore") -> None:
        self.refresh(core)

    def refresh(self, core: "AmoraCore", *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_refresh < self.REFRESH_SECONDS:
            self._expire_activity(now)
            return
        self._last_refresh = now
        try:
            report = core.health_report()
            modules = self._module_rows(core, report)
        except Exception as exc:
            self.record_error("console", f"health snapshot falhou: {type(exc).__name__}: {exc}")
            return
        with self._lock:
            self._modules = {row.module_id: row for row in modules}
        self._expire_activity(now)
        self.render(force=force)

    def _expire_activity(self, now: float) -> None:
        changed = False
        with self._lock:
            if self._activity_until and now >= self._activity_until:
                self._activity = "IDLE"
                self._activity_detail = "aguardando"
                self._activity_until = 0.0
                changed = True
        if changed:
            self.render()

    def _module_rows(self, core: "AmoraCore", report: dict[str, object]) -> list[ModuleStatus]:
        ok = report.get("status") == "ok"
        integrity = str(report.get("database_integrity") or "unknown")
        rows = [
            ModuleStatus("core", "Core", "ON" if ok else "DEGRADED", f"v{report.get('version', '?')}") ,
            ModuleStatus("database", "SQLite", "ON" if integrity == "ok" else "DEGRADED", f"schema {report.get('schema_version', '?')}") ,
            ModuleStatus("memory", "Memória", "ON", f"{report.get('memory_count', 0)} memórias"),
            ModuleStatus("world", "World Model", "ON", f"{report.get('canonical_world_state_count', 0)} claims ativos"),
            ModuleStatus("emotion", "Emoção", "ON", f"valência {report.get('mood_valence', 0)}"),
            ModuleStatus("autonomy", "Autonomia", "ON", str(report.get("active_intention_kind") or "idle")),
            ModuleStatus("cognition", "Cognição", "ON" if report.get("cognition_enabled") else "OFF", str(report.get("cognition_provider") or report.get("dialogue_provider") or "local")),
            ModuleStatus("groq.stt", "Groq STT", "ON" if report.get("groq_stt_configured") else "OFF", "Whisper"),
            ModuleStatus("groq.cognition", "Groq Cognição", "ON" if report.get("groq_cognition_configured") else "OFF", "principal"),
            ModuleStatus("gemini.cognition", "Gemini Cognição", "ON" if report.get("gemini_cognition_configured") else "OFF", "fallback"),
            ModuleStatus("gemini.tts", "Gemini Voz", "ON" if report.get("gemini_tts_configured", report.get("gemini_cognition_configured")) else "OFF", "expressiva"),
            ModuleStatus("android", "Android sessão", "ON" if int(report.get("active_sessions") or 0) > 0 else "OFF", f"{report.get('active_sessions', 0)} ativa(s)"),
        ]
        registry = getattr(getattr(core, "actions", None), "registry", None)
        if registry is not None:
            for manifest in registry.manifests():
                health = registry.health(manifest.plugin_id) if hasattr(registry, "health") else manifest.health
                raw = getattr(health, "value", str(health))
                state = {"healthy": "ON", "degraded": "DEGRADED", "unavailable": "OFF"}.get(raw, "OFF")
                rows.append(ModuleStatus(f"plugin:{manifest.plugin_id}", f"Plugin {manifest.plugin_id}", state, manifest.name))
        return rows

    def render(self, *, force: bool = False) -> None:
        if not self._started and not force:
            return
        with self._lock:
            rows = dict(self._modules)
            rows.update(self._overrides)
            activity = self._activity
            activity_detail = self._activity_detail
            notices = list(self._notices)
        text = self._compose(rows.values(), activity, activity_detail, notices)
        if not force and text == self._last_render:
            return
        self._last_render = text
        try:
            if self.interactive:
                self.stream.write("\x1b[2J\x1b[H")
                self.stream.write(text)
                self.stream.write("\n")
                self.stream.flush()
            elif force and notices:
                notice = notices[-1]
                suffix = f" x{notice.count}" if notice.count > 1 else ""
                self.stream.write(f"[amora] {notice.source}: {notice.message}{suffix}\n")
                self.stream.flush()
        except Exception:
            pass

    def _compose(
        self,
        modules,
        activity: str,
        activity_detail: str,
        notices: list[ConsoleNotice],
    ) -> str:
        width = max(72, min(112, shutil.get_terminal_size((96, 28)).columns))
        inner = width - 4
        purple = "\x1b[95m" if self.interactive else ""
        green = "\x1b[92m" if self.interactive else ""
        yellow = "\x1b[93m" if self.interactive else ""
        red = "\x1b[91m" if self.interactive else ""
        white = "\x1b[97m" if self.interactive else ""
        dim = "\x1b[2m" if self.interactive else ""
        reset = "\x1b[0m" if self.interactive else ""

        def clipped(value: str, limit: int) -> str:
            value = self._one_line(value)
            return value if len(value) <= limit else value[: max(1, limit - 1)] + "…"

        def state_text(state: str) -> str:
            state = state.upper()
            color = green if state == "ON" else yellow if state in {"WAIT", "DEGRADED"} else red
            return f"{color}{state:<8}{reset}"

        lines = [
            f"{purple}╭{'─' * (width - 2)}╮{reset}",
            f"{purple}│{reset} {white}AMORA CORE{reset}  {dim}{self.host}:{self.port}{reset}".ljust(width + len(purple + reset + white + reset + dim + reset) - 1) + f"{purple}│{reset}",
            f"{purple}├{'─' * (width - 2)}┤{reset}",
            f"{purple}│{reset} {white}ESTADO{reset}  {purple}{activity:<16}{reset} {clipped(activity_detail, inner - 26)}",
            f"{purple}├{'─' * (width - 2)}┤{reset}",
            f"{purple}│{reset} {white}MÓDULOS / PROVIDERS / PLUGINS{reset}",
        ]
        for row in modules:
            label = clipped(row.label, 24)
            detail = clipped(row.detail, max(8, inner - 39))
            lines.append(f"{purple}│{reset}  {label:<24} {state_text(row.state)} {dim}{detail}{reset}")

        lines.extend([
            f"{purple}├{'─' * (width - 2)}┤{reset}",
            f"{purple}│{reset} {white}ERROS RECENTES{reset}  {dim}(detalhes completos: {clipped(self.log_path, max(10, inner - 33))}){reset}",
        ])
        if not notices:
            lines.append(f"{purple}│{reset}  {dim}nenhum erro ativo{reset}")
        else:
            for notice in notices[-self.MAX_NOTICES :]:
                clock = time.strftime("%H:%M:%S", time.localtime(notice.timestamp))
                count = f" ×{notice.count}" if notice.count > 1 else ""
                msg = clipped(f"{notice.source}: {notice.message}{count}", inner - 11)
                lines.append(f"{purple}│{reset}  {red}{clock}{reset} {msg}")
        lines.append(f"{purple}╰{'─' * (width - 2)}╯{reset}")
        return "\n".join(lines)

    @staticmethod
    def _one_line(value: object) -> str:
        return " ".join(str(value or "").replace("\x00", "").split())
