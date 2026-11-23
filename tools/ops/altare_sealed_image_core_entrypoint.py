"""Core Docker entrypoint: decrypt only in memory, then exec the existing Core."""
from __future__ import annotations

import os
import sys
from pathlib import Path

from altare_sealed_image_crypto import SealedEnvError, unseal

BUNDLE = Path("/opt/amora/secrets/core.json")


def main() -> int:
    environment = dict(os.environ)
    key = environment.pop("AMORA_IMAGE_ENV_PRIVATE_KEY", "")
    if BUNDLE.is_file():
        if not key:
            print("SEALED_IMAGE_CORE_DECRYPT_KEY_MISSING", file=sys.stderr)
            return 78
        try:
            decoded = unseal(BUNDLE.read_bytes(), service="core", private_key_b64=key)
        except (SealedEnvError, OSError, ValueError):
            print("SEALED_IMAGE_CORE_DECRYPT_FAILED", file=sys.stderr)
            return 78
        environment.update(decoded)
    elif key:
        print("SEALED_IMAGE_CORE_BUNDLE_MISSING", file=sys.stderr)
        return 78
    # No plaintext .env file or private key survives in the running process.
    os.execvpe("amora", ["amora", "serve"], environment)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
