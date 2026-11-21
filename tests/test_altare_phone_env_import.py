"""Offline security and behavior contracts for Android/Termux file import."""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ops import altare_phone_env_import as module
from tools.ops.altare_env_handoff import EnvHandoffError

ORIGIN = "https://core-amora.go.altare.global"


def make_files(tmp_path, *, shared="same-value", discord_shared=None):
    core = tmp_path / "core" / ".env"
    discord = tmp_path / "discord" / ".env"
    core.parent.mkdir(parents=True)
    discord.parent.mkdir(parents=True)
    core.write_text("\n".join((
        "AMORA_POSTGRES_DSN=postgresql://example.net/dev",
        "AMORA_SECRET_LOCAL_API_TOKEN=admin-secret-value",
        f"AMORA_MESSAGING_SERVICE_TOKEN={shared}",
        "GROQ_API_KEY=provider-secret-value",
        "NF_BACKUP_API_TOKEN=must-not-transfer",
    )), encoding="utf-8")
    discord.write_text("\n".join((
        "DISCORD_BOT_TOKEN=discord-secret-value",
        f"AMORA_MESSAGING_SERVICE_TOKEN={discord_shared if discord_shared is not None else shared}",
        "GROQ_API_KEY=must-not-transfer",
        "AMORA_CORE_URL=https://old.northflank.app",
    )), encoding="utf-8")
    return core, discord


def test_offline_check_reads_both_files_without_network_or_secrets(tmp_path, monkeypatch, capsys):
    core, discord = make_files(tmp_path)
    monkeypatch.setattr(module, "check_altare_access",
                        lambda *_: pytest.fail("check must be fully offline"))
    monkeypatch.setattr(module, "apply",
                        lambda *_: pytest.fail("check must never apply"))
    assert module.main(["--core-file", str(core), "--discord-file", str(discord)]) == 0
    stdout = capsys.readouterr().out
    assert "ALTARE_LOCAL_ENV_CHECK_OK" in stdout
    for secret in ("admin-secret-value", "provider-secret-value", "discord-secret-value"):
        assert secret not in stdout


def test_plan_rewrites_core_origin_and_filters_secrets(tmp_path):
    core, discord = make_files(tmp_path)
    result = module.prepare_local_files(core_file=core, discord_file=discord, core_origin=ORIGIN)
    assert result["core"]["AMORA_PUBLIC_ORIGINS"] == ORIGIN
    assert result["discord"]["AMORA_CORE_URL"] == ORIGIN
    assert "DISCORD_BOT_TOKEN" not in result["core"]
    assert "AMORA_POSTGRES_DSN" not in result["discord"]
    assert "GROQ_API_KEY" not in result["discord"]
    assert "NF_BACKUP_API_TOKEN" not in result["core"]


def test_mismatch_is_rejected_before_network(tmp_path):
    core, discord = make_files(tmp_path, discord_shared="different-value")
    with pytest.raises(EnvHandoffError, match="ALTARE_SHARED_MESSAGING_TOKEN_MISMATCH"):
        module.prepare_local_files(core_file=core, discord_file=discord)


def test_path_detection_termux_downloads(tmp_path, monkeypatch):
    core, discord = make_files(tmp_path / "storage" / "downloads")
    monkeypatch.setattr(module.Path, "home", lambda: tmp_path)
    assert module.resolve_local_env("core") == core
    assert module.resolve_local_env("discord") == discord


def test_apply_requires_explicit_stopped_confirmation(tmp_path, monkeypatch, capsys):
    core, discord = make_files(tmp_path)
    monkeypatch.setattr(module, "check_altare_access",
                        lambda *_: pytest.fail("no network without confirmation"))
    assert module.main(["--core-file", str(core), "--discord-file", str(discord),
                       "--mode", "apply"]) == 2
    assert "ALTARE_APPLY_CONFIRM" in capsys.readouterr().err


def test_apply_checks_all_targets_before_first_write(tmp_path, monkeypatch, capsys):
    core, discord = make_files(tmp_path)
    monkeypatch.setenv("ALTARE_TOKEN_CONFIG", "alt_testing_secret")
    monkeypatch.setattr(module.shutil, "which", lambda exe: "/usr/local/bin/altr")
    events = []
    def verify(kind, token):
        assert token == "alt_testing_secret"
        events.append(("check", kind))
    def write(kind, env, token):
        events.append(("apply", kind))
        assert env["AMORA_MESSAGING_SERVICE_TOKEN"] == "same-value"
        return len(env)
    monkeypatch.setattr(module, "check_altare_access", verify)
    monkeypatch.setattr(module, "apply", write)
    assert module.main(["--core-file", str(core), "--discord-file", str(discord),
                       "--mode", "apply", "--confirm", module.APPLY_CONFIRMATION]) == 0
    assert events == [
        ("check", "core"), ("check", "discord"), ("apply", "core"), ("apply", "discord")
    ]
    stdout = capsys.readouterr().out
    assert "alt_testing_secret" not in stdout
    assert "discord-secret-value" not in stdout
    assert "ALTARE_LOCAL_ENV_IMPORT_FINISHED" in stdout


def test_missing_files_report_only_fixed_code(tmp_path, capsys):
    assert module.main(["--core-file", str(tmp_path / "missing")]) == 2
    err = capsys.readouterr().err
    assert err.strip() == "ALTARE_LOCAL_CORE_ENV_NOT_FOUND"
    assert str(tmp_path) not in err


def test_symlinked_sensitive_file_is_rejected(tmp_path):
    original, discord = make_files(tmp_path)
    alias = tmp_path / "core-link"
    alias.symlink_to(original)
    with pytest.raises(EnvHandoffError, match="SYMLINK_DENIED"):
        module.prepare_local_files(core_file=alias, discord_file=discord)


def test_apply_uses_signed_in_cli_when_no_team_token(tmp_path, monkeypatch, capsys):
    core, discord = make_files(tmp_path)
    monkeypatch.delenv("ALTARE_TOKEN_CONFIG", raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda exe: "/termux/usr/bin/altr")
    events = []

    def check(kind):
        events.append(("cli-check", kind))

    def write(kind, env, token):
        assert token == ""
        events.append(("apply", kind))
        return len(env)

    monkeypatch.setattr(module, "check_cli_session_access", check)
    monkeypatch.setattr(module, "check_altare_access",
                        lambda *_: pytest.fail("no team token: must use CLI login"))
    monkeypatch.setattr(module, "apply", write)
    assert module.main([
        "--core-file", str(core), "--discord-file", str(discord),
        "--mode", "apply", "--confirm", module.APPLY_CONFIRMATION,
    ]) == 0
    assert events == [
        ("cli-check", "core"), ("cli-check", "discord"),
        ("apply", "core"), ("apply", "discord"),
    ]
    result = capsys.readouterr().out
    assert "discord-secret-value" not in result
    assert "ALTARE_LOCAL_ENV_IMPORT_FINISHED" in result


def test_cli_session_access_is_readonly_and_suppresses_output(monkeypatch):
    import subprocess
    seen = []

    def fake_run(cmd, **kwargs):
        seen.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)
    module.check_cli_session_access("core")
    assert seen[0][0] == ["altr", "docker", "info", "Core-Amora", "--json"]
    assert seen[0][1]["stdout"] == subprocess.DEVNULL
    assert seen[0][1]["stderr"] == subprocess.DEVNULL
    assert "ALTARE_TOKEN" not in seen[0][1]["env"]


def test_cli_session_check_fails_closed_before_write(tmp_path, monkeypatch, capsys):
    import subprocess
    core, discord = make_files(tmp_path)
    monkeypatch.delenv("ALTARE_TOKEN_CONFIG", raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda exe: "/termux/usr/bin/altr")
    monkeypatch.setattr(module.subprocess, "run",
                        lambda cmd, **kwargs: subprocess.CompletedProcess(cmd, 1))
    monkeypatch.setattr(module, "apply",
                        lambda *_: pytest.fail("must not write without verified session"))
    assert module.main([
        "--core-file", str(core), "--discord-file", str(discord),
        "--mode", "apply", "--confirm", module.APPLY_CONFIRMATION,
    ]) == 2
    assert "ALTARE_CLI_READ_ACCESS_FAILED:CORE" in capsys.readouterr().err
