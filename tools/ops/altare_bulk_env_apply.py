"""Apply a private GitHub Actions dotenv/JSON secret to an existing Altare Docker App.

This script never prints environment values, credentials, URLs or CLI output.
Only run via the owner-controlled manual workflow; the app must be STOPPED.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
from urllib.parse import urlsplit

from tools.ops.altare_env_handoff import (
    EnvHandoffError, MAX_ALTARE_ENV_BYTES, MAX_ALTARE_VARIABLES,
    parse_source, prepare,
)

APP_NAMES = {"core": "Core-Amora", "discord": "Discord-Amora"}
SAFE_PATH = "/var/lib/amora/secrets/aiven-ca.pem"


def validate_origin(origin: str) -> str:
    """Accept a precise Altare-managed HTTPS hostname, not an old NF address."""
    try:
        u = urlsplit(origin)
        host = u.hostname or ""
        if (u.scheme == "https" and not u.username and not u.password
                and not u.port and not u.query and not u.fragment
                and u.path in ("", "/")
                and re.fullmatch(r"[a-z0-9-]+\.go\.altare\.global", host)):
            return f"https://{host}"
    except (TypeError, ValueError):
        pass
    raise EnvHandoffError("ALTARE_CORE_ORIGIN_INVALID")


def plan(bundle: str, *, service: str, core_origin: str) -> dict[str, str]:
    if service not in APP_NAMES:
        raise EnvHandoffError("ALTARE_SERVICE_UNKNOWN")
    if not bundle.strip():
        raise EnvHandoffError("ALTARE_SECRET_BUNDLE_MISSING")
    origin = validate_origin(core_origin)
    raw = parse_source(bundle)
    result, missing = prepare(raw, kind=service)
    if missing:
        raise EnvHandoffError("ALTARE_REQUIRED_NAMES_MISSING:" + ",".join(missing))
    # An old staging approval must never be applied to the PG18 production Core.
    if service == "core":
        result.pop("AMORA_PG_STAGING_APPROVAL", None)
        result.update({
            "AMORA_MODE": "cloud",
            "AMORA_STORAGE_BACKEND": "postgres",
            "AMORA_ASSET_STORAGE": "database",
            "AMORA_POSTGRES_CA_FILE": SAFE_PATH,
            "AMORA_PUBLIC_ORIGINS": origin,
        })
    else:
        result["AMORA_CORE_URL"] = origin
    if len(result) > MAX_ALTARE_VARIABLES:
        raise EnvHandoffError("HANDOFF_ALTARE_VARIABLE_LIMIT")
    size = sum(len(k.encode()) + len(v.encode()) + 2 for k, v in result.items())
    if size > MAX_ALTARE_ENV_BYTES:
        raise EnvHandoffError("HANDOFF_ALTARE_ENV_SIZE_LIMIT")
    return result


def apply(service: str, variables: dict[str, str], token: str) -> int:
    """Use only the documented altr docker env set API, with muted output."""
    if not token:
        raise EnvHandoffError("ALTARE_CONFIG_TOKEN_MISSING")
    app = APP_NAMES[service]
    proc_env = {
        "ALTARE_TOKEN": token,
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
    }
    completed = 0
    for name in sorted(variables):
        value = variables[name]
        try:
            # No shell, no echo and never forward CLI stdout/stderr to CI logs.
            run = subprocess.run(
                ["altr", "docker", "env", "set", app, f"{name}={value}"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=proc_env, timeout=40, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise EnvHandoffError("ALTARE_DOCKER_ENV_APPLY_FAILED:" + name) from None
        if run.returncode != 0:
            raise EnvHandoffError("ALTARE_DOCKER_ENV_APPLY_FAILED:" + name)
        completed += 1
    return completed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Safe bulk Altare Docker App env importer")
    parser.add_argument("--service", choices=("core", "discord"), required=True)
    parser.add_argument("--core-origin", required=True)
    parser.add_argument("--mode", choices=("check", "apply"), default="check")
    args = parser.parse_args(argv)
    try:
        variables = plan(
            os.environ.get("AMORA_PRIVATE_ENV_BUNDLE", ""),
            service=args.service, core_origin=args.core_origin,
        )
        if args.mode == "apply":
            count = apply(
                args.service, variables, os.environ.get("ALTARE_TOKEN_CONFIG", ""),
            )
            print(f"ALTARE_ENV_APPLIED service={args.service} count={count}")
        else:
            print(f"ALTARE_ENV_CHECK_OK service={args.service} count={len(variables)}")
        return 0
    except (EnvHandoffError, ValueError) as err:
        code = str(err)
        # Error codes only, and missing variable *names*, never values.
        if not re.fullmatch(r"[A-Za-z0-9_:,]+", code):
            code = "ALTARE_ENV_IMPORT_BLOCKED"
        print(code)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
