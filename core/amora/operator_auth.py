from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets

from amora.dev_auth import signing_secrets


OPERATOR_TOKEN_TTL_SECONDS = 12 * 60 * 60


def issue_operator_token(secret: str, clock, *, ttl_seconds: int = OPERATOR_TOKEN_TTL_SECONDS) -> str:
    """Issue a bounded browser credential for Web settings/console surfaces.

    Standard pairing signs with the admin secret. The optional BK dev-master
    path may sign the same bounded token shape with AMORA_DEV_MASTER_KEY; the
    raw master key itself is never a Bearer credential.
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
        signed = ("browser.v3." + payload).encode("utf-8")
        valid_signature = any(
            hmac.compare_digest(
                hmac.new(candidate.encode("utf-8"), signed, hashlib.sha256).hexdigest().encode("ascii"),
                signature.encode("ascii"),
            )
            for candidate in signing_secrets(secret)
        )
        if not valid_signature:
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
