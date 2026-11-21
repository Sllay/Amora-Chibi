"""Prepare and optionally import local phone .env files into existing Altare Docker Apps.

No Git uploads, image embedding, shell evaluation, or output of secret values.
The default mode only validates local files and NEVER contacts Altare.
The apply mode requires an explicit confirmation and local altr installation.
"""
from __future__ import annotations

import argparse
import os
import re
import shutil
import sys
from pathlib import Path

from tools.ops.altare_bulk_env_apply import (
    apply, check_altare_access, plan, validate_origin,
)
from tools.ops.altare_env_handoff import EnvHandoffError, MAX_BYTES

DEFAULT_CORE_ORIGIN = "https://core-amora.go.altare.global"
APPLY_CONFIRMATION = "BOTH_APPS_STOPPED"
EXPECTED_NAMES = {"core": "Core-Amora", "discord": "Discord-Amora"}


def resolve_local_env(kind: str, override: Path | None = None) -> Path:
    """Look in common Android/Termux Downloads roots, never Git or the network."""
    if kind not in EXPECTED_NAMES:
        raise EnvHandoffError("ALTARE_SERVICE_UNKNOWN")
    home = Path.home()
    candidates = [override] if override is not None else [
        home / "storage" / "downloads" / kind / ".env",
        Path("/storage/emulated/0/Download") / kind / ".env",
        Path("/storage/emulated/0/Downloads") / kind / ".env",
        Path("/sdcard/Download") / kind / ".env",
        home / "Downloads" / kind / ".env",
    ]
    for candidate in candidates:
        if candidate is None:
            continue
        try:
            if candidate.is_symlink():
                raise EnvHandoffError("ALTARE_LOCAL_SYMLINK_DENIED")
            if candidate.is_file():
                if candidate.stat().st_size > MAX_BYTES:
                    raise EnvHandoffError("HANDOFF_SOURCE_TOO_LARGE")
                return candidate
        except OSError:
            raise EnvHandoffError("ALTARE_LOCAL_FILE_UNREADABLE") from None
    raise EnvHandoffError("ALTARE_LOCAL_" + kind.upper() + "_ENV_NOT_FOUND")


def prepare_local_files(
    *, core_file: Path | None = None, discord_file: Path | None = None,
    core_origin: str = DEFAULT_CORE_ORIGIN,
) -> dict[str, dict[str, str]]:
    validate_origin(core_origin)
    result = {}
    for kind, path in (("core", core_file), ("discord", discord_file)):
        local = resolve_local_env(kind, path)
        try:
            raw = local.read_text(encoding="utf-8")
        except (OSError, UnicodeError):
            raise EnvHandoffError("ALTARE_LOCAL_FILE_UNREADABLE") from None
        result[kind] = plan(raw, service=kind, core_origin=core_origin)

    # Both workers must use exactly the same internal authorization token.
    shared = "AMORA_MESSAGING_SERVICE_TOKEN"
    if result["core"][shared] != result["discord"][shared]:
        raise EnvHandoffError("ALTARE_SHARED_MESSAGING_TOKEN_MISMATCH")
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Import Download/core/.env and Download/discord/.env without exposing keys"
    )
    parser.add_argument("--core-file", type=Path)
    parser.add_argument("--discord-file", type=Path)
    parser.add_argument("--core-origin", default=DEFAULT_CORE_ORIGIN)
    parser.add_argument("--mode", choices=("check", "apply"), default="check")
    parser.add_argument("--confirm", default="")
    parser.add_argument("--service", choices=("core", "discord", "both"), default="both")
    args = parser.parse_args(argv)

    try:
        variables = prepare_local_files(
            core_file=args.core_file,
            discord_file=args.discord_file,
            core_origin=args.core_origin,
        )
        selected = ("core", "discord") if args.service == "both" else (args.service,)
        if args.mode == "check":
            print(
                "ALTARE_LOCAL_ENV_CHECK_OK "
                + " ".join(f"{kind}_count={len(variables[kind])}" for kind in selected)
                + " mode=offline"
            )
            return 0

        # Changing Docker App variables can RESTART running services.
        # The operator must stop both Altare apps before applying anything.
        if args.confirm != APPLY_CONFIRMATION:
            raise EnvHandoffError("ALTARE_APPLY_CONFIRM_BOTH_APPS_STOPPED_REQUIRED")
        if shutil.which("altr") is None:
            raise EnvHandoffError("ALTARE_CLI_NOT_INSTALLED")
        token = os.environ.get("ALTARE_TOKEN_CONFIG", "")
        if not token:
            raise EnvHandoffError("ALTARE_CONFIG_TOKEN_MISSING")

        # Resolve and authorize all selected targets before any mutation.
        for kind in selected:
            check_altare_access(kind, token)
        for kind in selected:
            count = apply(kind, variables[kind], token)
            print(f"ALTARE_LOCAL_ENV_APPLIED service={kind} count={count}")
        print("ALTARE_LOCAL_ENV_IMPORT_FINISHED apps_remain_under_operator_control")
        return 0
    except (EnvHandoffError, OSError, UnicodeError, ValueError) as exc:
        # Only finite application error codes, never filenames, tokens,
        # provider response bodies, or the contents of .env files.
        code = str(exc) if isinstance(exc, EnvHandoffError) else "ALTARE_LOCAL_IO_ERROR"
        if not re.fullmatch(r"[A-Za-z0-9_:,]+", code):
            code = "ALTARE_LOCAL_ENV_IMPORT_BLOCKED"
        print(code, file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
