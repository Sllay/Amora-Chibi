"""Encrypt the native PostgreSQL 18 snapshot and verify its contents.

The only published artifact is an AES-256-encrypted archive. Never print
private rows, memory contents, plaintext hashes, password or Aiven DSN.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pyzipper


ROOT = Path("/tmp/amora-aiven-pg18-export")
SOURCE = ROOT / "amora-aiven-pg18-v18-prewrite.dump"
ARCHIVE = ROOT / "output" / "amora-aiven-pg18-backup-encrypted.zip"


def _digest(path: Path) -> bytes:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.digest()


def seal() -> None:
    secret = os.getenv("AMORA_BACKUP_PASSPHRASE", "")
    if len(secret) < 32:
        raise RuntimeError("PG_BACKUP_PASSPHRASE_NOT_CONFIGURED")
    if not SOURCE.is_file() or SOURCE.stat().st_size < 4096:
        raise RuntimeError("PG_BACKUP_SOURCE_NOT_READY")
    password = secret.encode("utf-8")
    original = _digest(SOURCE)
    ARCHIVE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with pyzipper.AESZipFile(
        ARCHIVE, "w", compression=pyzipper.ZIP_DEFLATED, allowZip64=True,
    ) as dest:
        dest.setpassword(password)
        dest.setencryption(pyzipper.WZ_AES, nbits=256)
        dest.write(SOURCE, arcname=SOURCE.name)
    with pyzipper.AESZipFile(ARCHIVE, "r") as archived:
        archived.setpassword(password)
        assert archived.namelist() == [SOURCE.name]
        info = archived.getinfo(SOURCE.name)
        if not (info.flag_bits & 1) or info.wz_aes_strength != 3:
            raise RuntimeError("PG_BACKUP_ENCRYPTION_UNVERIFIED")
        copied = hashlib.sha256()
        with archived.open(SOURCE.name) as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                copied.update(chunk)
        if copied.digest() != original:
            raise RuntimeError("PG_BACKUP_ENCRYPTED_HASH_MISMATCH")
    print("AIVEN_PG18_AES256_ARCHIVE_VALIDATED true", flush=True)


if __name__ == "__main__":
    seal()
