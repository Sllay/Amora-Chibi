from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets


OPERATOR_TOKEN_TTL_SECONDS = 12 * 60 * 60


def issue_operator_token(secret: str, clock, *, ttl_seconds: int = OPERATOR_TOKEN_TTL_SECONDS) -> str:
    """Issue a bounded browser credential for Web settings/console surfaces.

    It is signed by the admin secret so rotating LOCAL_API_TOKEN revokes every
    operator credential, but the admin secret itself is never sent to the browser.
    """
    if ttl_seconds <= 0:
        raise ValueError("operator token ttl must be positive")
    expiry = int(clock.now().timestamp()) + int(ttl_seconds)
    raw = f"operator:{expiry}:{secrets.token_hex(16)}"
    payload = base64.urlsafe_b64encode(raw.encode("utf-8")).decode("ascii").rstrip("=")
    prefix = "browser.v3."
    signature = hmac.new(secret.encode("utf-8"), (prefix + payload).encode("utf-8"), hashlib.sha256).hexdigest()
    return prefix + payload + "." + signature


def operator_token_role(token: str, secret: str, clock) -> str | None:
    try:
        prefix, version, payload, signature = token.split(".")
        if prefix != "browser" or version != "v3" or len(payload) > 256:
            return None
        expected = hmac.new(
            secret.encode("utf-8"),
            ("browser.v3." + payload).encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected.encode("ascii"), signature.encode("ascii")):
            return None
        decoded = base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)).decode("utf-8")
        role, expiry_text, nonce = decoded.split(":")
        if role != "operator" or int(expiry_text) <= int(clock.now().timestamp()):
            return None
        if not re.fullmatch(r"[a-f0-9]{32}", nonce):
            return None
        return "operator"
    except (ValueError, UnicodeError):
        return None
