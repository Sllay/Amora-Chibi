"""Prepare private, per-service Altare environment bundles from Northflank exports.

Runs OFFLINE. This tool NEVER calls Northflank or Altare, prints secret
values or writes a file in Git. Imported values remain on the operator's
machine until manually submitted to Altare and securely deleted.

Accepts either dotenv KEY=VALUE lines (no shell evaluation) or JSON:
  {"KEY":"value", ...}
  {"data":{"runtimeEnvironment":{"KEY":"value"}}}
  {"secrets":{"variables":{"KEY":"value"}}}
"""
from __future__ import annotations

import argparse
import json
import os
import re
from pathlib import Path
from typing import Mapping

NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
# Machine and provider metadata must be configured for the NEW deployment.
NOT_PORTABLE = {
    "HOME", "PORT", "SERVER_PORT", "AMORA_HOST", "AMORA_CORE_URL",
    "AMORA_PUBLIC_ORIGINS", "AMORA_POSTGRES_CA_FILE",
    "AMORA_DURABLE_FILESYSTEM", "AMORA_WORKER_HEALTH_PORT",
    "AMORA_BROWSER_URL", "AMORA_WEB_BROWSER_URL",
    # A Northflank-only operator endpoint is a live call into the old host,
    # even if the generic Core/Discord variables were moved correctly.
    "AMORA_PCS4_DEV_ENABLED", "AMORA_PCS4_NF_PROJECT",
    "AMORA_PCS4_NF_JOB", "AMORA_PCS4_NF_TOKEN",
    "AMORA_PCS4_NF_TEAM",
    # Credentials for Altare administration belong in CI secrets, not
    # application environments. Never migrate a legacy host control token.
    "AMORA_HOME", "AMORA_TRUSTED_PROXY_IPS",
    "AMORA_WEB_BROWSER_PRIVATE_HTTP",
}
NOT_PORTABLE_PREFIXES = ("NF_", "NORTHFLANK_", "ALTARE_", "GITHUB_")
# Do not transfer chat-provider keys, Aiven credentials or admin secrets into
# the Discord Worker just because they were present in an inherited NF group.
DISCORD_ALLOWED = frozenset({
    "DISCORD_BOT_TOKEN", "DISCORD_APPLICATION_ID", "DISCORD_TEST_GUILD_ID",
    "AMORA_MESSAGING_SERVICE_TOKEN", "AMORA_PLATFORM_ACCOUNT_SCOPE",
    "AMORA_PLATFORM_REMOTE_PROCESSING", "AMORA_WEB_SCREENSHOTS_ENABLED",
    "AMORA_WEB_SCREENSHOT_MICROLINK_API_KEY",
    "AMORA_WEB_SCREENSHOT_DAILY_LIMIT",
})
REQUIRED = {
    "core": {"AMORA_POSTGRES_DSN", "AMORA_MESSAGING_SERVICE_TOKEN",
             "AMORA_SECRET_LOCAL_API_TOKEN"},
    "discord": {"DISCORD_BOT_TOKEN", "AMORA_MESSAGING_SERVICE_TOKEN"},
}
MAX_BYTES = 128 * 1024
MAX_KEYS = 220
MAX_ALTARE_VARIABLES = 200
MAX_ALTARE_ENV_BYTES = 32_000


class EnvHandoffError(ValueError):
    """Stable no-secret errors suitable for terminal output."""


def _mapping(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        raise EnvHandoffError("HANDOFF_JSON_OBJECT_REQUIRED")
    for key in ("data", "secrets"):
        if isinstance(value.get(key), dict):
            inner = value[key]
            for nested in ("runtimeEnvironment", "variables"):
                if isinstance(inner.get(nested), dict):
                    value = inner[nested]
                    break
    if not isinstance(value, dict):
        raise EnvHandoffError("HANDOFF_JSON_OBJECT_REQUIRED")
    if len(value) > MAX_KEYS:
        raise EnvHandoffError("HANDOFF_TOO_MANY_VARIABLES")
    out: dict[str, str] = {}
    for k, v in value.items():
        if not isinstance(k, str) or not NAME.fullmatch(k) or not isinstance(v, str):
            raise EnvHandoffError("HANDOFF_INVALID_KEY_OR_VALUE")
        if "\x00" in v or "\r" in v:
            raise EnvHandoffError("HANDOFF_CONTROL_CHAR_DENIED")
        if len(v.encode("utf-8")) > 8192:
            raise EnvHandoffError("HANDOFF_VALUE_TOO_LARGE")
        out[k] = v
    return out


def parse_source(raw: str) -> dict[str, str]:
    if len(raw.encode("utf-8")) > MAX_BYTES:
        raise EnvHandoffError("HANDOFF_SOURCE_TOO_LARGE")
    if raw.lstrip().startswith("{"):
        try:
            return _mapping(json.loads(raw))
        except (json.JSONDecodeError, TypeError):
            raise EnvHandoffError("HANDOFF_JSON_INVALID") from None
    result: dict[str, str] = {}
    for line in raw.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        if "=" not in line:
            raise EnvHandoffError("HANDOFF_DOTENV_SYNTAX_INVALID")
        key, value = line.split("=", 1)
        key = key.strip()
        if not NAME.fullmatch(key) or key in result:
            raise EnvHandoffError("HANDOFF_DUPLICATE_OR_INVALID_NAME")
        value = value.strip()
        if value.startswith('"'):
            try:
                value = json.loads(value)
            except json.JSONDecodeError:
                raise EnvHandoffError("HANDOFF_DOUBLE_QUOTE_INVALID") from None
        elif value.startswith("'"):
            if len(value) < 2 or not value.endswith("'"):
                raise EnvHandoffError("HANDOFF_SINGLE_QUOTE_INVALID")
            value = value[1:-1]
        result[key] = value
    return _mapping(result)


def prepare(source: Mapping[str, str], *, kind: str) -> tuple[dict[str, str], tuple[str, ...]]:
    if kind not in REQUIRED:
        raise EnvHandoffError("HANDOFF_SERVICE_UNSUPPORTED")
    safe: dict[str, str] = {}
    for key, value in source.items():
        if key in NOT_PORTABLE or key.startswith(NOT_PORTABLE_PREFIXES):
            continue
        if "\n" in value:
            # Complex secret-file values require mounted file preparation,
            # not an unreliable escaped env line.
            raise EnvHandoffError("HANDOFF_MULTILINE_VALUE_REQUIRES_SECRET_FILE")
        if kind == "discord" and key not in DISCORD_ALLOWED:
            continue
        if key.startswith("DISCORD_") and kind == "core":
            continue
        safe[key] = value
    if len(safe) > MAX_ALTARE_VARIABLES:
        raise EnvHandoffError("HANDOFF_ALTARE_VARIABLE_LIMIT")
    encoded_size = sum(
        len(key.encode("utf-8")) + len(value.encode("utf-8")) + 2
        for key, value in safe.items()
    )
    if encoded_size > MAX_ALTARE_ENV_BYTES:
        raise EnvHandoffError("HANDOFF_ALTARE_ENV_SIZE_LIMIT")
    missing = tuple(sorted(REQUIRED[kind].difference(safe)))
    return safe, missing


def write_private(path: Path, variables: Mapping[str, str]) -> None:
    if path.exists():
        raise EnvHandoffError("HANDOFF_REFUSE_OVERWRITE")
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path.parent, 0o700)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as target:
            for key in sorted(variables):
                value = variables[key]
                # json.dumps makes dotenv quoting deterministic without
                # introducing any shell expansion. Never execute/source it.
                target.write(key + "=" + json.dumps(value, ensure_ascii=False) + "\n")
    except BaseException:
        path.unlink(missing_ok=True)
        raise


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare private Altare service envs offline")
    parser.add_argument("--core", type=Path, required=True)
    parser.add_argument("--discord", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.output_dir.exists() and any(args.output_dir.iterdir()):
            raise EnvHandoffError("HANDOFF_OUTPUT_DIRECTORY_NOT_EMPTY")
        prepared = {}
        missing_by_service = {}
        for kind in ("core", "discord"):
            input_file = getattr(args, kind)
            values = parse_source(input_file.read_text(encoding="utf-8"))
            filtered, missing = prepare(values, kind=kind)
            prepared[kind] = filtered
            if missing:
                missing_by_service[kind] = missing
        if missing_by_service:
            for kind, missing in missing_by_service.items():
                print(kind + ": missing variable NAMES " + ",".join(missing))
            return 2
        for kind, filtered in prepared.items():
            write_private(args.output_dir / (".env.altare-" + kind), filtered)
            print(kind + ": " + str(len(filtered)) + " names written to private file")
        print("HANDOFF_READY: transfer privately, validate, delete sources and outputs")
        return 0
    except (OSError, ValueError, UnicodeError) as error:
        reason = str(error) if isinstance(error, EnvHandoffError) else "HANDOFF_IO_ERROR"
        print("HANDOFF_BLOCKED " + reason)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
