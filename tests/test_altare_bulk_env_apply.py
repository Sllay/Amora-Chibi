"""No-network security and portability tests for private Altare env CI import."""
import subprocess
import sys
from pathlib import Path

import pytest

# Core's editable package adds /core, not necessarily the repository root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ops.altare_bulk_env_apply import plan, apply, validate_origin
from tools.ops.altare_env_handoff import EnvHandoffError


ORIGIN = "https://core-amora.go.altare.global"


def test_core_candidate_filters_legacy_host_and_sets_new_origin():
    bundle = (
        "AMORA_POSTGRES_DSN=postgresql://alice:secret@aiven.example/main\n"
        "AMORA_MESSAGING_SERVICE_TOKEN=" + "t" * 40 + "\n"
        "AMORA_SECRET_LOCAL_API_TOKEN=" + "a" * 40 + "\n"
        "AMORA_PUBLIC_ORIGINS=https://old.northflank.app\n"
        "AMORA_POSTGRES_CA_FILE=/oldhost/aiven.pem\n"
        "AMORA_PG_STAGING_APPROVAL=old-approval\n"
        "NF_BACKUP_API_TOKEN=do-not-include\n"
        "GROQ_API_KEY=provider-value\n"
    )
    got = plan(bundle, service="core", core_origin=ORIGIN)
    assert got["AMORA_POSTGRES_CA_FILE"] == "/var/lib/amora/secrets/aiven-ca.pem"
    assert got["AMORA_PUBLIC_ORIGINS"] == ORIGIN
    assert got["AMORA_STORAGE_BACKEND"] == "postgres"
    assert got["GROQ_API_KEY"] == "provider-value"
    assert "NF_BACKUP_API_TOKEN" not in got
    assert "AMORA_PG_STAGING_APPROVAL" not in got
    assert "DISCORD_BOT_TOKEN" not in got


def test_discord_filters_provider_secrets_and_points_at_new_core():
    bundle = {
        "DISCORD_BOT_TOKEN": "bot-value",
        "AMORA_MESSAGING_SERVICE_TOKEN": "t" * 40,
        "AMORA_CORE_URL": "https://old.northflank.app",
        "AMORA_WEB_SCREENSHOTS_ENABLED": "1",
        "AMORA_PLATFORM_REMOTE_PROCESSING": "1",
        "GROQ_API_KEY": "must-never-reach-discord",
    }
    import json
    got = plan(json.dumps(bundle), service="discord", core_origin=ORIGIN)
    assert got["AMORA_CORE_URL"] == ORIGIN
    assert got["AMORA_PLATFORM_REMOTE_PROCESSING"] == "1"
    assert got["AMORA_WEB_SCREENSHOTS_ENABLED"] == "1"
    assert "GROQ_API_KEY" not in got


@pytest.mark.parametrize("bad", [
    "", "http://core-amora.go.altare.global",
    "https://old.northflank.app",
    "https://core-amora.go.altare.global.evil.test",
    "https://admin@core-amora.go.altare.global",
    "https://core-amora.go.altare.global/secret",
])
def test_origin_fail_closed(bad):
    with pytest.raises(EnvHandoffError):
        validate_origin(bad)


def test_cli_no_shell_and_no_stdout_leak(monkeypatch):
    seen = []
    def fake_run(cmd, **kwargs):
        seen.append((cmd, kwargs))
        return subprocess.CompletedProcess(cmd, 0)
    monkeypatch.setattr(subprocess, "run", fake_run)
    count = apply("discord", {"DISCORD_BOT_TOKEN": "sensitive-test"},
                  "alt-token-test")
    assert count == 1
    cmd, kwargs = seen[0]
    assert cmd == ["altr", "docker", "env", "set", "Discord-Amora",
                   "DISCORD_BOT_TOKEN=sensitive-test"]
    assert kwargs["stdout"] == subprocess.DEVNULL
    assert kwargs["stderr"] == subprocess.DEVNULL
    assert kwargs["env"]["ALTARE_TOKEN"] == "alt-token-test"
    assert "AMORA_PRIVATE_ENV_BUNDLE" not in kwargs["env"]


def test_cli_failure_only_emits_variable_name(monkeypatch):
    monkeypatch.setattr(subprocess, "run",
                        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1))
    with pytest.raises(EnvHandoffError, match="ALTARE_DOCKER_ENV_APPLY_FAILED:DISCORD_BOT_TOKEN"):
        apply("discord", {"DISCORD_BOT_TOKEN": "SECRET_VALUE"}, "token")
