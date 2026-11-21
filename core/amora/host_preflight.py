"""Host portability preflight for the Amora Core/Discord Altare cutover.

This is a fail-closed diagnostic, not a deploy or secret mover. Its JSON
output contains ONLY fixed reason codes, NEVER environment values.
Run within each prospective Altare container before enabling the gateway.
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from amora.deployment_profiles import get_profile

PG18_APPROVAL = "AUTHORIZE_VERIFIED_AIVEN_PG18_CORE_CUTOVER"
PRODUCTION_HOST = "altare"


def _origin(value: str) -> bool:
    try:
        u = urlsplit(value)
        return (u.scheme == "https" and bool(u.hostname) and
                not u.username and not u.password and not u.query and
                not u.fragment and u.path in ("", "/") and
                "northflank" not in u.hostname.lower())
    except (ValueError, TypeError):
        return False


def inspect_core(env: dict[str, str], *, ca_file_readable: bool,
                 data_folder_writable: bool, profile: str = "") -> list[str]:
    """Return ordered reason enums; never echo DSNs or tokens."""
    errors = []
    selected = profile or get_profile().host
    if selected != PRODUCTION_HOST:
        errors.append("HOST_PROFILE_MUST_BE_ALTARE")
    if env.get("AMORA_MODE") != "cloud":
        errors.append("CORE_CLOUD_MODE_REQUIRED")
    if env.get("AMORA_STORAGE_BACKEND") != "postgres":
        errors.append("CORE_PG18_BACKEND_REQUIRED")
    if env.get("AMORA_PG_PRODUCTION_APPROVAL") != PG18_APPROVAL:
        errors.append("CORE_PG18_APPROVAL_REQUIRED")
    if not env.get("AMORA_POSTGRES_DSN"):
        errors.append("CORE_AIVEN_DSN_REQUIRED")
    if not all(env.get(name) for name in (
        "AMORA_STAGING_EXPECTED_HOST", "AMORA_STAGING_EXPECTED_PORT",
        "AMORA_STAGING_EXPECTED_DATABASE",
    )):
        errors.append("CORE_AIVEN_ENDPOINT_PIN_REQUIRED")
    ca = env.get("AMORA_POSTGRES_CA_FILE", "")
    if not (ca and Path(ca).is_absolute() and ca_file_readable):
        errors.append("CORE_AIVEN_CA_MOUNT_REQUIRED")
    if not _origin(env.get("AMORA_PUBLIC_ORIGINS", "")):
        errors.append("CORE_PUBLIC_ORIGIN_MUST_BE_ALTARE_HTTPS")
    if env.get("AMORA_PCS4_DEV_ENABLED") == "1" or any(
        env.get(key) for key in (
            "AMORA_PCS4_NF_TOKEN", "AMORA_PCS4_NF_JOB", "AMORA_PCS4_NF_PROJECT"
        )
    ):
        errors.append("CORE_NORTHFLANK_JOB_MUST_BE_DISABLED")
    if any(env.get(name) for name in (
        "NORTHFLANK_API_TOKEN", "NF_BACKUP_API_TOKEN",
    )):
        errors.append("CORE_OLD_HOST_API_TOKEN_DENIED")
    if env.get("AMORA_TRUSTED_PROXY_IPS"):
        errors.append("CORE_REVIEW_ALTARE_PROXY_PEERS")
    if env.get("AMORA_WEB_BROWSER_PRIVATE_HTTP") == "1":
        errors.append("CORE_BROWSER_HTTP_UNREVIEWED")
    if env.get("AMORA_WEB_BROWSER_URL") and not _origin(
        env["AMORA_WEB_BROWSER_URL"]
    ):
        errors.append("CORE_BROWSER_URL_MUST_BE_ALTARE_HTTPS")
    if not env.get("AMORA_SECRET_LOCAL_API_TOKEN") or len(
        env.get("AMORA_SECRET_LOCAL_API_TOKEN", "").encode()
    ) < 32:
        errors.append("CORE_ADMIN_TOKEN_MISSING")
    if not env.get("AMORA_MESSAGING_SERVICE_TOKEN") or len(
        env.get("AMORA_MESSAGING_SERVICE_TOKEN", "").encode()
    ) < 32:
        errors.append("CORE_WORKER_AUTH_TOKEN_MISSING")
    if env.get("AMORA_ASSET_STORAGE") != "database":
        if not (env.get("AMORA_DURABLE_FILESYSTEM", "").lower() == "true"
                and data_folder_writable):
            errors.append("CORE_ASSET_PERSISTENCE_REQUIRED")
    if not data_folder_writable:
        errors.append("CORE_DATA_FOLDER_NOT_WRITABLE")
    return errors


def inspect_discord(env: dict[str, str], *, core_token: str,
                    gateway_fenced: bool) -> list[str]:
    """Gateway safety is an EXTERNAL cutover assertion, not an env flag."""
    issues = []
    if not _origin(env.get("AMORA_CORE_URL", "")):
        issues.append("DISCORD_CORE_URL_MUST_BE_ALTARE_HTTPS")
    if not env.get("DISCORD_BOT_TOKEN"):
        issues.append("DISCORD_BOT_TOKEN_REQUIRED")
    token = env.get("AMORA_MESSAGING_SERVICE_TOKEN", "")
    if not token or len(token.encode()) < 32:
        issues.append("DISCORD_WORKER_TOKEN_MISSING")
    elif not core_token or token != core_token:
        issues.append("DISCORD_CORE_WORKER_TOKEN_MISMATCH")
    if not gateway_fenced:
        issues.append("DISCORD_GATEWAY_MUST_BE_FENCED")
    if any(env.get(name) for name in (
        "NF_BACKUP_API_TOKEN", "NORTHFLANK_API_TOKEN", "AMORA_POSTGRES_DSN",
        "AMORA_SECRET_LOCAL_API_TOKEN",
    )):
        issues.append("DISCORD_EXCESS_PRIVILEGE_SECRET")
    return issues


def _writable_folder(path: str) -> bool:
    """Readiness does not create files in a mount it does not control."""
    try:
        p = Path(path)
        return p.is_dir() and os.access(p, os.W_OK | os.X_OK)
    except (ValueError, OSError):
        return False


def preflight(env: dict[str, str] | None = None, *, candidate_altare: bool = False
              ) -> dict[str, object]:
    """Candidate checks do not change production host selection or gateway state."""
    provided = dict(os.environ if env is None else env)
    ca = provided.get("AMORA_POSTGRES_CA_FILE", "")
    ca_readable = False
    try:
        if ca:
            path = Path(ca)
            ca_readable = path.is_file() and os.access(path, os.R_OK)
    except (ValueError, OSError):
        pass
    home = provided.get("AMORA_HOME", "/var/lib/amora")
    core = inspect_core(
        provided, ca_file_readable=ca_readable,
        data_folder_writable=_writable_folder(home),
        profile="altare" if candidate_altare else "",
    )
    # This CLI runs inside the Core. Discord separately needs this same
    # routine with its env supplied by a trusted operator or staging gate.
    return {"status": "ready" if not core else "blocked",
            "provider": "altare", "component": "core", "reason_codes": core,
            "note": "No claims about Docker host volume persistence or Discord fencing."}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check prospective Altare Core configuration")
    parser.add_argument(
        "--candidate-altare", action="store_true",
        help="check isolated Altare staging without changing active host",
    )
    args = parser.parse_args(argv)
    result = preflight(candidate_altare=args.candidate_altare)
    print(json.dumps(result, sort_keys=True))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    raise SystemExit(main())
