from __future__ import annotations

import hmac
import os


DEV_MASTER_KEY_ENV = "AMORA_DEV_MASTER_KEY"
DEV_MASTER_KEY_MIN_BYTES = 32
DEV_MASTER_KEY_MAX_BYTES = 512


def _equal(first: str, second: str) -> bool:
    return hmac.compare_digest(first.encode("utf-8"), second.encode("utf-8"))


def configured_dev_master_key() -> str | None:
    value = os.getenv(DEV_MASTER_KEY_ENV)
    if value is None or value == "":
        return None
    return value


def validate_dev_master_key(primary_secret: str, *, cloud: bool) -> str | None:
    """Return the configured BK master key after strict runtime validation.

    The key is an authentication root only. It is never accepted as a Bearer
    credential itself; browser credentials are HMAC-signed with it locally.
    """
    key = configured_dev_master_key()
    if key is None:
        return None
    encoded = key.encode("utf-8")
    if len(encoded) < DEV_MASTER_KEY_MIN_BYTES:
        raise ValueError("AMORA_DEV_MASTER_KEY must contain at least 32 bytes")
    if len(encoded) > DEV_MASTER_KEY_MAX_BYTES:
        raise ValueError("AMORA_DEV_MASTER_KEY must contain at most 512 bytes")
    if any(ord(char) < 32 or ord(char) == 127 for char in key):
        raise ValueError("AMORA_DEV_MASTER_KEY must not contain control characters")
    if not primary_secret:
        if cloud:
            raise ValueError("AMORA_DEV_MASTER_KEY requires AMORA_SECRET_LOCAL_API_TOKEN in cloud")
        return key

    distinct = {
        "Core admin token": primary_secret,
        "messaging service token": os.getenv("AMORA_MESSAGING_SERVICE_TOKEN", ""),
        "voice service token": os.getenv("AMORA_VOICE_SERVICE_TOKEN", ""),
    }
    for label, other in distinct.items():
        if other and _equal(key, other):
            raise ValueError(f"AMORA_DEV_MASTER_KEY must differ from the {label}")
    return key


def signing_secrets(primary_secret: str) -> tuple[str, ...]:
    """Secrets accepted only for signed browser credential verification."""
    master = validate_dev_master_key(
        primary_secret,
        cloud=os.getenv("AMORA_MODE", "local") == "cloud",
    )
    if master is None:
        return (primary_secret,)
    return (primary_secret, master)
