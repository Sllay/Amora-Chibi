"""Authenticated X25519/AES-256-GCM envelope for Altare image env bundles.

Only ciphertext may be embedded in a Docker image. Private keys exist solely
in the Altare app's runtime environment and local operator backups.
"""
from __future__ import annotations

import base64
import json
import os
import re
from typing import Mapping

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import x25519
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

FORMAT = "amora-sealed-env-v1"
NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}\Z")
SERVICES = frozenset({"core", "discord"})
MAX_CIPHERTEXT = 65536
MAX_PLAINTEXT = 32768


class SealedEnvError(ValueError):
    """Error must be reported as a static code; never print the input."""


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(raw: str, *, max_bytes: int) -> bytes:
    if not isinstance(raw, str) or len(raw) > 4 * max_bytes:
        raise SealedEnvError("SEALED_ENV_INVALID_BASE64")
    try:
        decoded = base64.b64decode(raw, validate=True)
    except (ValueError, base64.binascii.Error):
        raise SealedEnvError("SEALED_ENV_INVALID_BASE64") from None
    if len(decoded) > max_bytes:
        raise SealedEnvError("SEALED_ENV_INPUT_TOO_LARGE")
    return decoded


def _check_payload(payload: object, *, service: str) -> dict[str, str]:
    if service not in SERVICES or not isinstance(payload, dict) or not payload:
        raise SealedEnvError("SEALED_ENV_INVALID_PAYLOAD")
    if len(payload) > 200:
        raise SealedEnvError("SEALED_ENV_TOO_MANY_VARIABLES")
    out: dict[str, str] = {}
    for name, value in payload.items():
        if (not isinstance(name, str) or not NAME.fullmatch(name)
                or name.startswith(("ALTARE_", "LD_"))
                or name in {"AMORA_IMAGE_ENV_PRIVATE_KEY", "PATH", "NODE_OPTIONS",
                            "NODE_PATH", "PYTHONPATH", "PYTHONHOME", "ENV", "BASH_ENV",
                            "__proto__", "constructor", "prototype"}
                or not isinstance(value, str) or "\0" in value or "\n" in value
                or "\r" in value):
            raise SealedEnvError("SEALED_ENV_INVALID_VARIABLE")
        out[name] = value
    required = (
        {"AMORA_POSTGRES_DSN", "AMORA_MESSAGING_SERVICE_TOKEN",
         "AMORA_SECRET_LOCAL_API_TOKEN"} if service == "core"
        else {"DISCORD_BOT_TOKEN", "AMORA_MESSAGING_SERVICE_TOKEN"}
    )
    if any(not out.get(k, "").strip() for k in required):
        raise SealedEnvError("SEALED_ENV_REQUIRED_VARIABLE_MISSING")
    if service == "discord" and any(
        name.startswith(("GROQ_", "OPENAI_", "AIVEN_", "AMORA_POSTGRES_"))
        for name in out
    ):
        raise SealedEnvError("SEALED_ENV_DISCORD_SCOPE_VIOLATION")
    encoded = json.dumps(out, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    if len(encoded) > MAX_PLAINTEXT:
        raise SealedEnvError("SEALED_ENV_PLAINTEXT_TOO_LARGE")
    return out


def _derive(shared: bytes, *, salt: bytes, service: str) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(), length=32, salt=salt,
        info=(FORMAT + ":" + service).encode("ascii"),
    ).derive(shared)


def seal(payload: Mapping[str, str], *, service: str, public_key_b64: str) -> bytes:
    clean = _check_payload(dict(payload), service=service)
    try:
        public_key = serialization.load_der_public_key(_unb64(public_key_b64, max_bytes=256))
    except (ValueError, TypeError):
        raise SealedEnvError("SEALED_ENV_PUBLIC_KEY_INVALID") from None
    if not isinstance(public_key, x25519.X25519PublicKey):
        raise SealedEnvError("SEALED_ENV_PUBLIC_KEY_INVALID")
    ephemeral = x25519.X25519PrivateKey.generate()
    salt, nonce = os.urandom(16), os.urandom(12)
    encrypted = AESGCM(
        _derive(ephemeral.exchange(public_key), salt=salt, service=service)
    ).encrypt(
        nonce,
        json.dumps(clean, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        (FORMAT + ":" + service).encode("ascii"),
    )
    return json.dumps({
        "format": FORMAT,
        "service": service,
        "ephemeral_public_key": _b64(ephemeral.public_key().public_bytes(
            serialization.Encoding.Raw, serialization.PublicFormat.Raw
        )),
        "salt": _b64(salt),
        "nonce": _b64(nonce),
        "ciphertext": _b64(encrypted),
    }, sort_keys=True, separators=(",", ":")).encode("ascii")


def unseal(envelope_bytes: bytes, *, service: str, private_key_b64: str) -> dict[str, str]:
    if service not in SERVICES or len(envelope_bytes) > MAX_CIPHERTEXT:
        raise SealedEnvError("SEALED_ENV_BUNDLE_INVALID")
    try:
        env = json.loads(envelope_bytes)
    except (ValueError, UnicodeError):
        raise SealedEnvError("SEALED_ENV_BUNDLE_INVALID") from None
    if (not isinstance(env, dict) or env.get("format") != FORMAT
            or env.get("service") != service):
        raise SealedEnvError("SEALED_ENV_SERVICE_MISMATCH")
    try:
        private_key = serialization.load_der_private_key(
            _unb64(private_key_b64, max_bytes=256), password=None,
        )
        if not isinstance(private_key, x25519.X25519PrivateKey):
            raise ValueError("wrong key kind")
        ephemeral_raw = _unb64(env["ephemeral_public_key"], max_bytes=32)
        salt = _unb64(env["salt"], max_bytes=16)
        nonce = _unb64(env["nonce"], max_bytes=12)
        ciphertext = _unb64(env["ciphertext"], max_bytes=MAX_CIPHERTEXT)
        if len(ephemeral_raw) != 32 or len(salt) != 16 or len(nonce) != 12:
            raise ValueError("invalid envelope lengths")
        ephemeral = x25519.X25519PublicKey.from_public_bytes(ephemeral_raw)
        plain = AESGCM(
            _derive(private_key.exchange(ephemeral), salt=salt, service=service)
        ).decrypt(
            nonce, ciphertext, (FORMAT + ":" + service).encode("ascii"),
        )
        if len(plain) > MAX_PLAINTEXT:
            raise ValueError("plaintext too large")
        payload = json.loads(plain)
    except (KeyError, TypeError, ValueError, UnicodeError, InvalidTag):
        raise SealedEnvError("SEALED_ENV_DECRYPT_OR_FORMAT_FAILED") from None
    return _check_payload(payload, service=service)
