from __future__ import annotations

from io import StringIO
from pathlib import Path

from amora.actions.models import PluginHealth
from amora.app import AmoraCore
from amora.server_console import ServerConsole


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap()


def test_server_console_lists_core_provider_and_plugin_health(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    console = ServerConsole(
        host="127.0.0.1",
        port=8765,
        log_path=str(core.paths.logs / "amora.jsonl"),
        stream=StringIO(),
        interactive=False,
    )
    try:
        rows = {row.module_id: row for row in console._module_rows(core, core.health_report())}
        assert rows["core"].state == "ON"
        assert rows["database"].state == "ON"
        assert "groq.cognition" in rows
        assert "gemini.tts" in rows
        assert rows["plugin:core.actions"].state == "ON"

        core.actions.registry.set_health("core.actions", PluginHealth.UNAVAILABLE)
        rows = {row.module_id: row for row in console._module_rows(core, core.health_report())}
        assert rows["plugin:core.actions"].state == "OFF"
    finally:
        console.close()
        core.close()


def test_server_console_deduplicates_repeating_errors(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    console = ServerConsole(
        host="127.0.0.1",
        port=8765,
        log_path=str(core.paths.logs / "amora.jsonl"),
        stream=StringIO(),
        interactive=False,
    )
    try:
        console.start(core)
        console.record_error("Gemini Voz", "quota exhausted")
        console.record_error("Gemini Voz", "quota exhausted")
        assert len(console._notices) == 1
        assert console._notices[-1].count == 2
    finally:
        console.close()
        core.close()
