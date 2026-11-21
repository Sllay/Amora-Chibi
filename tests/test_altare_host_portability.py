"""Provider portability: Altare keeps all runtime paths and APIs explicit."""
from __future__ import annotations

from pathlib import Path

import pytest

from amora.host_preflight import inspect_core, inspect_discord
from amora.storage.pcs4_dev import NorthflankRecovery


def core_variables():
    return {
        "AMORA_MODE": "cloud",
        "AMORA_STORAGE_BACKEND": "postgres",
        "AMORA_PG_PRODUCTION_APPROVAL":
            "AUTHORIZE_VERIFIED_AIVEN_PG18_CORE_CUTOVER",
        "AMORA_POSTGRES_DSN": "fake-not-used-in-this-unit-test",
        "AMORA_STAGING_EXPECTED_HOST": "example.aivencloud.com",
        "AMORA_STAGING_EXPECTED_PORT": "18236",
        "AMORA_STAGING_EXPECTED_DATABASE": "defaultdb",
        "AMORA_POSTGRES_CA_FILE": "/var/lib/amora/secrets/aiven-ca.pem",
        "AMORA_ASSET_STORAGE": "database",
        "AMORA_PUBLIC_ORIGINS": "https://amora-core.go.altare.global",
        "AMORA_MESSAGING_SERVICE_TOKEN": "t" * 40,
        "AMORA_SECRET_LOCAL_API_TOKEN": "a" * 40,
    }


def worker_variables():
    return {
        "DISCORD_BOT_TOKEN": "fake-not-used",
        "AMORA_CORE_URL": "https://amora-core.go.altare.global",
        "AMORA_MESSAGING_SERVICE_TOKEN": "t" * 40,
        "AMORA_PLATFORM_ACCOUNT_SCOPE": "primary",
    }


def test_altare_core_requires_real_ca_mount_and_writable_data_folder():
    env = core_variables()
    assert inspect_core(env, ca_file_readable=True,
                        data_folder_writable=True, profile="altare") == []
    blocked = inspect_core(env, ca_file_readable=False,
                           data_folder_writable=False, profile="altare")
    assert "CORE_AIVEN_CA_MOUNT_REQUIRED" in blocked
    assert "CORE_DATA_FOLDER_NOT_WRITABLE" in blocked
    assert "CORE_AIVEN_DSN_REQUIRED" not in blocked


def test_legacy_northflank_jobs_or_stale_host_keys_block_altare():
    env = core_variables()
    env["AMORA_PCS4_DEV_ENABLED"] = "1"
    env["AMORA_PCS4_NF_TOKEN"] = "NEVER_PRINT_THIS"
    env["NF_BACKUP_API_TOKEN"] = "ANOTHER_PRIVATE_TOKEN"
    env["AMORA_PUBLIC_ORIGINS"] = "https://old.northflank.app"
    env["AMORA_WEB_BROWSER_PRIVATE_HTTP"] = "1"
    problems = inspect_core(env, ca_file_readable=True,
                            data_folder_writable=True, profile="altare")
    assert set(problems) >= {
        "CORE_NORTHFLANK_JOB_MUST_BE_DISABLED",
        "CORE_OLD_HOST_API_TOKEN_DENIED",
        "CORE_PUBLIC_ORIGIN_MUST_BE_ALTARE_HTTPS",
        "CORE_BROWSER_HTTP_UNREVIEWED",
    }
    joined = " ".join(problems)
    assert "NEVER_PRINT_THIS" not in joined
    assert "ANOTHER_PRIVATE_TOKEN" not in joined


def test_assets_require_explicit_persistent_storage():
    env = core_variables()
    env.pop("AMORA_ASSET_STORAGE")
    assert "CORE_ASSET_PERSISTENCE_REQUIRED" in inspect_core(
        env, ca_file_readable=True,
        data_folder_writable=True, profile="altare")
    env["AMORA_DURABLE_FILESYSTEM"] = "true"
    assert "CORE_ASSET_PERSISTENCE_REQUIRED" not in inspect_core(
        env, ca_file_readable=True,
        data_folder_writable=True, profile="altare")


def test_discord_needs_new_https_origin_and_single_gateway_confirmation():
    env = worker_variables()
    assert inspect_discord(env, core_token="t" * 40,
                           gateway_fenced=True) == []
    assert "DISCORD_GATEWAY_MUST_BE_FENCED" in inspect_discord(
        env, core_token="t" * 40, gateway_fenced=False)
    env["AMORA_CORE_URL"] = "https://old.northflank.app"
    env["AMORA_MESSAGING_SERVICE_TOKEN"] = "different-long-token-1234567890123456"
    got = inspect_discord(env, core_token="t" * 40,
                          gateway_fenced=False)
    assert "DISCORD_CORE_URL_MUST_BE_ALTARE_HTTPS" in got
    assert "DISCORD_CORE_WORKER_TOKEN_MISMATCH" in got


def test_old_host_recovery_controller_stays_disabled_on_altare(monkeypatch):
    monkeypatch.setenv("ALTARE", "1")
    monkeypatch.setenv("AMORA_PCS4_DEV_ENABLED", "1")
    monkeypatch.setenv("AMORA_PCS4_NF_TOKEN", "should-not-leave-host")
    monkeypatch.setenv("AMORA_PCS4_NF_PROJECT", "default")
    monkeypatch.setenv("AMORA_PCS4_NF_JOB", "recovery")
    assert NorthflankRecovery.from_environment() is None


def test_old_northflank_archive_fenced_by_selected_source_profile():
    workflow = (
        Path(__file__).resolve().parents[1]
        / ".github/workflows/northflank-encrypted-archive.yml"
    ).read_text()
    assert "from amora.deployment_profiles import get_profile" in workflow
    assert "steps.hostcheck.outputs.active == 'true'" in workflow
    assert "needs.archive.outputs.old_host_active == 'true'" in workflow


def test_secret_handoff_strips_old_volume_and_host_control_values():
    from tools.ops.altare_env_handoff import prepare
    env = {
        **core_variables(),
        "AMORA_HOME": "/old-northflank-volume",
        "AMORA_PCS4_DEV_ENABLED": "1",
        "AMORA_PCS4_NF_TOKEN": "very-secret",
        "AMORA_TRUSTED_PROXY_IPS": "10.0.0.0/8",
        "AMORA_WEB_BROWSER_PRIVATE_HTTP": "1",
    }
    values, missing = prepare(env, kind="core")
    assert not missing
    for banned in (
        "AMORA_HOME", "AMORA_PCS4_NF_TOKEN", "AMORA_PCS4_DEV_ENABLED",
        "AMORA_TRUSTED_PROXY_IPS", "AMORA_WEB_BROWSER_PRIVATE_HTTP",
        "AMORA_POSTGRES_CA_FILE",
    ):
        assert banned not in values



def test_discord_environment_is_strict_allowlist_not_core_provider_secrets():
    from tools.ops.altare_env_handoff import prepare
    env = {
        "DISCORD_BOT_TOKEN": "discord-value",
        "DISCORD_APPLICATION_ID": "123",
        "AMORA_MESSAGING_SERVICE_TOKEN": "z" * 40,
        "NUMES_API_KEY": "NUMES_MUST_NOT_LEAK",
        "GROQ_API_KEY": "GROQ_MUST_NOT_LEAK",
        "OPENROUTER_API_KEY": "OPENROUTER_MUST_NOT_LEAK",
        "AMORA_SECRET_LOCAL_API_TOKEN": "ADMIN_MUST_NOT_LEAK",
        "AMORA_POSTGRES_DSN": "DATABASE_MUST_NOT_LEAK",
        "AMORA_WEB_SCREENSHOT_MICROLINK_API_KEY": "microlink-worker-only",
        "AMORA_WEB_SCREENSHOT_DAILY_LIMIT": "4",
    }
    filtered, missing = prepare(env, kind="discord")
    assert not missing
    assert set(filtered) == {
        "DISCORD_BOT_TOKEN", "DISCORD_APPLICATION_ID",
        "AMORA_MESSAGING_SERVICE_TOKEN",
        "AMORA_WEB_SCREENSHOT_MICROLINK_API_KEY",
        "AMORA_WEB_SCREENSHOT_DAILY_LIMIT",
    }
