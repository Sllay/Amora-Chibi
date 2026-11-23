"""Create two X25519 key pairs locally without showing private keys in stdout.

Requires openssl CLI. Files are written only under Termux's private HOME,
never into the Git checkout, Downloads, GitHub or a Docker image.
"""
from __future__ import annotations

import argparse
import base64
import os
import subprocess
import sys
from pathlib import Path

from tools.ops.altare_env_handoff import EnvHandoffError

DEFAULT_DIR = Path.home() / ".config" / "amora" / "altare-image-keys"


def _write_new(path: Path, value: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "w", encoding="ascii") as f:
        f.write(value + "\n")


def generate(path: Path = DEFAULT_DIR) -> None:
    if path.exists() and (path.is_symlink() or any(path.iterdir())):
        raise EnvHandoffError("SEALED_KEYGEN_REFUSE_OVERWRITE")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(path, 0o700)
    for service in ("core", "discord"):
        try:
            p = subprocess.run(
                ["openssl", "genpkey", "-algorithm", "X25519", "-outform", "DER"],
                stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=20, check=True,
            )
            pub = subprocess.run(
                ["openssl", "pkey", "-inform", "DER", "-pubout", "-outform", "DER"],
                input=p.stdout, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                timeout=20, check=True,
            )
        except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
            raise EnvHandoffError("SEALED_KEYGEN_OPENSSL_FAILED") from None
        _write_new(
            path / (service + "-private.b64"),
            base64.b64encode(p.stdout).decode("ascii"),
        )
        _write_new(
            path / (service + "-public.b64"),
            base64.b64encode(pub.stdout).decode("ascii"),
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate private per-app Altare env keys")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_DIR)
    args = parser.parse_args(argv)
    try:
        generate(args.output_dir)
        print("SEALED_KEYGEN_OK: private files under Termux HOME, two public keys ready")
        return 0
    except (EnvHandoffError, OSError):
        print("SEALED_KEYGEN_BLOCKED", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
