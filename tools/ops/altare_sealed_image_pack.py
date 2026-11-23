"""Seal GitHub Actions bundles for Docker builds; never place plaintext in layers."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from tools.ops.altare_bulk_env_apply import plan
from tools.ops.altare_env_handoff import EnvHandoffError
from tools.ops.altare_sealed_image_crypto import SealedEnvError, seal

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare encrypted-only image env secret")
    parser.add_argument("--service", choices=("core", "discord"), required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--core-origin", default="https://core-amora.go.altare.global")
    args = parser.parse_args(argv)
    try:
        raw = os.environ.get("AMORA_PRIVATE_ENV_BUNDLE", "")
        key = os.environ.get("AMORA_IMAGE_PUBLIC_KEY", "")
        if not raw or not key:
            raise SealedEnvError("SEALED_ENV_BUILD_INPUT_MISSING")
        vars_prepared = plan(raw, service=args.service, core_origin=args.core_origin)
        encrypted = seal(vars_prepared, service=args.service, public_key_b64=key)
        # No plaintext file is ever written. The file is in RUNNER_TEMP,
        # outside Docker context, and is consumed as a BuildKit secret mount.
        args.output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        descriptor = os.open(args.output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as f:
            f.write(encrypted)
        print("SEALED_IMAGE_BUNDLE_READY service=" + args.service
              + " variables=" + str(len(vars_prepared)))
        return 0
    except (EnvHandoffError, SealedEnvError, OSError, ValueError):
        # Never print exception text or input. Only bounded failure code.
        print("SEALED_IMAGE_BUNDLE_BLOCKED", file=sys.stderr)
        return 2

if __name__ == "__main__":
    raise SystemExit(main())
