"""Northflank-to-Altare env export must never print credentials or mix scopes."""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

# Core CI adds /core, not necessarily repository root, to the Python path.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from tools.ops.altare_env_handoff import (
    EnvHandoffError, parse_source, prepare, write_private, main,
)


def test_northflank_api_json_and_dotenv_are_parsed_without_execution():
    data = {"data": {"runtimeEnvironment": {
        "DISCORD_BOT_TOKEN": "token=SENSITIVE#with$quotes",
        "AMORA_MESSAGING_SERVICE_TOKEN": "m" * 36,
        "AMORA_CORE_URL": "https://northflank.old",
    }}}
    assert parse_source(json.dumps(data))["DISCORD_BOT_TOKEN"].startswith("token=")
    env = 'DISCORD_BOT_TOKEN="token=SENSITIVE#with$quotes"\nAMORA_MESSAGING_SERVICE_TOKEN=' + "m" * 36
    assert parse_source(env)["DISCORD_BOT_TOKEN"] == "token=SENSITIVE#with$quotes"
    assert parse_source("export A='value with spaces'\nB=two=equals") == {
        "A": "value with spaces", "B": "two=equals",
    }


def test_scopes_exclude_host_specific_and_core_secrets_from_discord():
    raw = {
        "DISCORD_BOT_TOKEN": "supersecret",
        "AMORA_MESSAGING_SERVICE_TOKEN": "m" * 36,
        "AMORA_SECRET_LOCAL_API_TOKEN": "a" * 40,
        "AMORA_POSTGRES_DSN": "postgresql://owner:secret@aiven",
        "AMORA_POSTGRES_CA_FILE": "/northflank/secrets/ca.pem",
        "AMORA_CORE_URL": "https://northflank.old",
        "PORT": "8731",
        "NF_BACKUP_API_TOKEN": "northflank-data",
        "AMORA_PLATFORM_ACCOUNT_SCOPE": "primary",
    }
    core, core_missing = prepare(raw, kind="core")
    worker, worker_missing = prepare(raw, kind="discord")
    assert not core_missing and not worker_missing
    assert "DISCORD_BOT_TOKEN" not in core
    assert "AMORA_POSTGRES_DSN" not in worker
    assert "AMORA_SECRET_LOCAL_API_TOKEN" not in worker
    for values in (core, worker):
        assert "PORT" not in values
        assert "NF_BACKUP_API_TOKEN" not in values
        assert "AMORA_POSTGRES_CA_FILE" not in values
        assert "AMORA_CORE_URL" not in values


def test_no_shell_expansion_and_private_mode(tmp_path):
    target = tmp_path / "private" / ".env.altare-core"
    write_private(target, {"AMORA_SECRET_LOCAL_API_TOKEN": "$(cat /secrets)"})
    assert target.stat().st_mode & 0o077 == 0
    assert target.parent.stat().st_mode & 0o077 == 0
    assert "$(cat /secrets)" in target.read_text()
    with pytest.raises(EnvHandoffError, match="REFUSE_OVERWRITE"):
        write_private(target, {})


@pytest.mark.parametrize("invalid", [
    '{"KEY":123}', '{"bad-key":"a"}', "MALFORMED", "A=1\nA=2",
    "x" * 150000, '{"A":"\\u0000"}',
])
def test_malformed_inputs_fail_closed(invalid):
    with pytest.raises(EnvHandoffError):
        parse_source(invalid)


def test_invalid_multiline_secret_file_is_not_silently_transformed():
    with pytest.raises(EnvHandoffError, match="MULTILINE"):
        prepare({
            "AMORA_MESSAGING_SERVICE_TOKEN": "secret",
            "AMORA_POSTGRES_DSN": "a",
            "AMORA_SECRET_LOCAL_API_TOKEN": "b",
            "PEM_DATA": "line1\nline2",
        }, kind="core")


def test_cli_does_not_expose_secret_values_in_output(tmp_path, capsys):
    core = tmp_path / "core.json"
    discord = tmp_path / "discord.env"
    core.write_text(json.dumps({
        "AMORA_POSTGRES_DSN": "SECRET_DATABASE_KEY",
        "AMORA_MESSAGING_SERVICE_TOKEN": "SECRET_MESSAGING_KEY",
        "AMORA_SECRET_LOCAL_API_TOKEN": "SECRET_ADMIN_KEY",
    }))
    discord.write_text(
        "DISCORD_BOT_TOKEN=SECRET_DISCORD_KEY\n"
        "AMORA_MESSAGING_SERVICE_TOKEN=SECRET_MESSAGING_KEY\n"
    )
    out = tmp_path / "new"
    assert main(["--core", str(core), "--discord", str(discord),
                 "--output-dir", str(out)]) == 0
    stdout = capsys.readouterr().out
    assert "SECRET_" not in stdout
    assert (out / ".env.altare-core").exists()
    assert (out / ".env.altare-discord").exists()



def test_required_values_cannot_be_blank_even_if_names_are_present():
    core, missing = prepare({
        "AMORA_POSTGRES_DSN": "   ",
        "AMORA_MESSAGING_SERVICE_TOKEN": "m" * 40,
        "AMORA_SECRET_LOCAL_API_TOKEN": "",
    }, kind="core")
    assert missing == ("AMORA_POSTGRES_DSN", "AMORA_SECRET_LOCAL_API_TOKEN")
    discord, worker_missing = prepare({
        "DISCORD_BOT_TOKEN": "",
        "AMORA_MESSAGING_SERVICE_TOKEN": "m" * 40,
    }, kind="discord")
    assert worker_missing == ("DISCORD_BOT_TOKEN",)


def test_altare_app_env_limits_fail_closed_before_private_output():
    minimum = {
        "AMORA_POSTGRES_DSN": "postgresql://host",
        "AMORA_MESSAGING_SERVICE_TOKEN": "a" * 36,
        "AMORA_SECRET_LOCAL_API_TOKEN": "b" * 36,
    }
    many = {f"TEST_KEY_{n}": "abc" for n in range(200)}
    with pytest.raises(EnvHandoffError, match="VARIABLE_LIMIT"):
        prepare({**minimum, **many}, kind="core")
    huge = {"LARGE_" + str(i): "x" * 7000 for i in range(5)}
    with pytest.raises(EnvHandoffError, match="ENV_SIZE_LIMIT"):
        prepare({**minimum, **huge}, kind="core")
