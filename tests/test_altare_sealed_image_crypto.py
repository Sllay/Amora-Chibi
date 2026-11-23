"""Encrypted image env contracts: AEAD, separation, cross-language, no leaks."""
from __future__ import annotations

import base64
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytest.importorskip("cryptography")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools" / "ops"))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import x25519

from tools.ops.altare_sealed_image_crypto import (
    FORMAT, SealedEnvError, seal, unseal,
)
from tools.ops import altare_sealed_image_core_entrypoint as entrypoint


def keys():
    private = x25519.X25519PrivateKey.generate()
    public = private.public_key()
    return (
        base64.b64encode(public.public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo,
        )).decode("ascii"),
        base64.b64encode(private.private_bytes(
            serialization.Encoding.DER, serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )).decode("ascii"),
    )


def vars_for(kind):
    if kind == "core":
        return {
            "AMORA_POSTGRES_DSN": "postgresql://alice:secret-value@example.net/app",
            "AMORA_MESSAGING_SERVICE_TOKEN": "m" * 40,
            "AMORA_SECRET_LOCAL_API_TOKEN": "a" * 40,
            "GROQ_API_KEY": "SENSITIVE_CORE_ONLY",
        }
    return {
        "DISCORD_BOT_TOKEN": "SENSITIVE_DISCORD_BOT",
        "AMORA_MESSAGING_SERVICE_TOKEN": "m" * 40,
        "AMORA_CORE_URL": "https://core-amora.go.altare.global",
    }


@pytest.mark.parametrize("kind", ["core", "discord"])
def test_seal_only_contains_ciphertext_and_roundtrips(kind):
    public, private = keys()
    vars_input = vars_for(kind)
    bundle = seal(vars_input, service=kind, public_key_b64=public)
    for value in vars_input.values():
        assert value.encode() not in bundle
    assert json.loads(bundle)["format"] == FORMAT
    assert unseal(bundle, service=kind, private_key_b64=private) == vars_input


def test_wrong_private_key_and_tampered_ciphertext_fail_closed():
    public, private = keys()
    _other_public, wrong_private = keys()
    bundle = seal(vars_for("discord"), service="discord", public_key_b64=public)
    with pytest.raises(SealedEnvError):
        unseal(bundle, service="discord", private_key_b64=wrong_private)
    envelope = json.loads(bundle)
    raw = bytearray(base64.b64decode(envelope["ciphertext"]))
    raw[-1] ^= 0x10
    envelope["ciphertext"] = base64.b64encode(raw).decode("ascii")
    with pytest.raises(SealedEnvError):
        unseal(json.dumps(envelope).encode(), service="discord", private_key_b64=private)


def test_encrypted_core_cannot_be_decrypted_as_discord():
    public, private = keys()
    bundle = seal(vars_for("core"), service="core", public_key_b64=public)
    with pytest.raises(SealedEnvError, match="SERVICE_MISMATCH"):
        unseal(bundle, service="discord", private_key_b64=private)


@pytest.mark.parametrize("bad", [
    {"PATH": "/evil"},
    {"LD_PRELOAD": "/evil"},
    {"NODE_OPTIONS": "--require=evil"},
    {"AMORA_IMAGE_ENV_PRIVATE_KEY": "leak"},
    {"A": "contains\nnewline"},
])
def test_injection_and_multiline_env_are_rejected(bad):
    public, _ = keys()
    with pytest.raises(SealedEnvError):
        seal({**vars_for("core"), **bad}, service="core", public_key_b64=public)


def test_core_entrypoint_executed_without_key_in_child_environment(tmp_path, monkeypatch):
    public, private = keys()
    core = vars_for("core")
    bundle = tmp_path / "core.json"
    bundle.write_bytes(seal(core, service="core", public_key_b64=public))
    monkeypatch.setattr(entrypoint, "BUNDLE", bundle)
    monkeypatch.setenv("AMORA_IMAGE_ENV_PRIVATE_KEY", private)
    seen = []
    monkeypatch.setattr(entrypoint.os, "execvpe",
                        lambda file, args, env: seen.append((file, args, env)))
    assert entrypoint.main() == 1
    assert len(seen) == 1
    assert seen[0][1] == ["amora", "serve"]
    assert seen[0][2]["GROQ_API_KEY"] == "SENSITIVE_CORE_ONLY"
    assert "AMORA_IMAGE_ENV_PRIVATE_KEY" not in seen[0][2]


def test_core_wrong_key_blocks_process_start(tmp_path, monkeypatch, capsys):
    public, _ = keys()
    _, wrong_private = keys()
    bundle = tmp_path / "core.json"
    bundle.write_bytes(seal(vars_for("core"), service="core", public_key_b64=public))
    monkeypatch.setattr(entrypoint, "BUNDLE", bundle)
    monkeypatch.setenv("AMORA_IMAGE_ENV_PRIVATE_KEY", wrong_private)
    monkeypatch.setattr(entrypoint.os, "execvpe",
                        lambda *_: pytest.fail("invalid key must not execute core"))
    assert entrypoint.main() == 78
    err = capsys.readouterr().err
    assert err.strip() == "SEALED_IMAGE_CORE_DECRYPT_FAILED"
    assert wrong_private not in err


@pytest.mark.skipif(shutil.which("node") is None, reason="Node 22 unavailable")
def test_cross_language_python_seal_node_discord_decrypt():
    public, private = keys()
    encrypted = seal(vars_for("discord"), service="discord", public_key_b64=public)
    script = """
    import { decryptDiscordEnv } from './workers/discord/env-bundle.mjs';
    const data = decryptDiscordEnv(
      Buffer.from(process.env.SYNTHETIC_ENVELOPE, 'base64'),
      process.env.SYNTHETIC_PRIVATE_KEY
    );
    if (data.DISCORD_BOT_TOKEN !== 'SENSITIVE_DISCORD_BOT' ||
        data.AMORA_MESSAGING_SERVICE_TOKEN !== 'm'.repeat(40)) process.exit(2);
    console.log('NODE_DECRYPT_OK');
    """
    proc_env = {
        **os.environ,
        "SYNTHETIC_ENVELOPE": base64.b64encode(encrypted).decode(),
        "SYNTHETIC_PRIVATE_KEY": private,
    }
    run = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=Path(__file__).resolve().parents[1], env=proc_env,
        capture_output=True, text=True, timeout=15,
    )
    assert run.returncode == 0, "NODE_DECRYPT_FAILED"
    assert run.stdout.strip() == "NODE_DECRYPT_OK"
    assert "SENSITIVE_DISCORD_BOT" not in run.stdout


def test_sealed_pack_writes_only_ciphertext(tmp_path, monkeypatch, capsys):
    from tools.ops.altare_sealed_image_pack import main
    from tools.ops.altare_bulk_env_apply import plan
    public, private = keys()
    raw = "\n".join(f"{k}={v}" for k, v in vars_for("core").items())
    monkeypatch.setenv("AMORA_PRIVATE_ENV_BUNDLE", raw)
    monkeypatch.setenv("AMORA_IMAGE_PUBLIC_KEY", public)
    output = tmp_path / "sealed.json"
    assert main(["--service", "core", "--output", str(output)]) == 0
    assert "SENSITIVE_CORE_ONLY" not in output.read_text()
    assert "SENSITIVE_CORE_ONLY" not in capsys.readouterr().out
    assert unseal(output.read_bytes(), service="core", private_key_b64=private)["GROQ_API_KEY"] == "SENSITIVE_CORE_ONLY"
    assert output.stat().st_mode & 0o077 == 0
