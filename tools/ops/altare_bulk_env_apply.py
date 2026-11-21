"""Apply a private GitHub Actions dotenv/JSON secret to an existing Altare Docker App.

This script never prints environment values, credentials, URLs or CLI output.
Only run via the owner-controlled manual workflow; the app must be STOPPED.
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import json
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, HTTPRedirectHandler, build_opener

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


class _BlockRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _read_altare_json(path: str, token: str) -> dict:
    """Fixed-origin GET only; do not leak HTTP body or exception into logs."""
    if not re.fullmatch(r"/[A-Za-z0-9/_-]+", path):
        raise EnvHandoffError("ALTARE_API_PATH_INVALID")
    req = Request(
        "https://console.altare.gg/api" + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    try:
        with build_opener(_BlockRedirect()).open(req, timeout=12) as resp:
            raw = resp.read(131073)
            if len(raw) > 131072:
                raise EnvHandoffError("ALTARE_API_TOO_LARGE")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise EnvHandoffError("ALTARE_API_RESPONSE_INVALID")
            return data
    except HTTPError as exc:
        if exc.code == 401:
            raise EnvHandoffError("ALTARE_API_TOKEN_UNAUTHORIZED_401") from None
        if exc.code == 403:
            stage = "SELF" if path == "/api-tokens/self" else "APPS"
            raise EnvHandoffError("ALTARE_API_SCOPE_DENIED_403_" + stage) from None
        raise EnvHandoffError("ALTARE_API_UNAVAILABLE") from None
    except (URLError, OSError, TimeoutError, UnicodeError, ValueError):
        raise EnvHandoffError("ALTARE_API_UNAVAILABLE") from None


def check_altare_access(service: str, token: str) -> None:
    """Authenticate and verify least privileges + exact target app visibility."""
    if service not in APP_NAMES or not token.startswith("alt_"):
        raise EnvHandoffError("ALTARE_CONFIG_TOKEN_MISSING")
    metadata = _read_altare_json("/api-tokens/self", token)
    scopes = metadata.get("scopes")
    team = metadata.get("team")
    if not isinstance(scopes, list) or not {"servers:view", "servers:settings"} <= set(scopes):
        raise EnvHandoffError("ALTARE_API_SCOPES_INSUFFICIENT")
    team_id = team.get("id") if isinstance(team, dict) else None
    if not isinstance(team_id, str) or not re.fullmatch(r"[A-Za-z0-9-]+", team_id):
        raise EnvHandoffError("ALTARE_TEAM_NOT_RESOLVED")
    apps = _read_altare_json(f"/tenants/{team_id}/servers", token).get("items")
    if not isinstance(apps, list) or sum(
        1 for app in apps if isinstance(app, dict) and app.get("name") == APP_NAMES[service]
    ) != 1:
        raise EnvHandoffError("ALTARE_TARGET_APP_NOT_VISIBLE")


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
        token = os.environ.get("ALTARE_TOKEN_CONFIG", "")
        check_altare_access(args.service, token)
        if args.mode == "apply":
            count = apply(
                args.service, variables, token,
            )
            print(f"ALTARE_ENV_APPLIED service={args.service} count={count}")
        else:
            print(f"ALTARE_ENV_CHECK_OK service={args.service} count={len(variables)} access=verified")
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
