from __future__ import annotations

import asyncio
from types import SimpleNamespace

import httpx
import pytest
from fastapi import HTTPException

from amora.art.routes import ArtGenerateRequest, authorize_art_request
from amora.art.service import (
    ArtError, ArtService, OvhSdxlBackend, OVH_SDXL_URL, STYLE_VERSION,
    detect_image,
)


PNG = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + (
    1024
).to_bytes(4, "big") * 2 + b"\x08\x02\x00\x00\x00" + b"\x00" * 40


class FakeSecrets:
    def get(self, name):
        assert name == "OVH_AI_ENDPOINTS_ACCESS_TOKEN"
        return "not-a-real-credential"


class FakeBackend:
    name = "fake"
    supports_lora = False
    supports_references = False

    def __init__(self):
        self.calls = []

    async def generate(self, prompt, negative_prompt):
        self.calls.append((prompt, negative_prompt))
        return PNG, "image/png"


@pytest.mark.asyncio
async def test_canonical_style_is_kept_outside_provider_and_deterministic():
    backend = FakeBackend()
    service = ArtService(FakeSecrets(), backend=backend)
    first = await service.generate("sorrindo", remote_processing_allowed=True)
    second = await service.generate("sorrindo", remote_processing_allowed=True)
    assert first.image == PNG
    assert first.style_version == STYLE_VERSION
    assert first.prompt_sha256 == second.prompt_sha256
    assert "hand-drawn" in backend.calls[0][0]
    assert len(backend.calls) == 2
    assert service.capabilities()["custom_lora"] is False


@pytest.mark.asyncio
async def test_requires_explicit_remote_processing_and_never_drops_unsupported_controls():
    backend = FakeBackend()
    service = ArtService(FakeSecrets(), backend=backend)
    for kwargs, expected in [
        ({}, "ART_REMOTE_PROCESSING_NOT_ALLOWED"),
        ({"remote_processing_allowed": True, "reference_ids": ("front",)}, "ART_REFERENCES_UNSUPPORTED"),
        ({"remote_processing_allowed": True, "lora_id": "my-style"}, "ART_LORA_UNSUPPORTED"),
        ({"remote_processing_allowed": True, "preset": "unknown"}, "ART_PRESET_INVALID"),
    ]:
        with pytest.raises(ArtError) as exc:
            await service.generate("teste", **kwargs)
        assert exc.value.code == expected
    assert backend.calls == []


@pytest.mark.asyncio
async def test_ovh_dedicated_endpoint_uses_negative_prompt_and_bounded_image():
    calls = []
    def transport(req):
        calls.append(req)
        return httpx.Response(200, content=PNG)
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        service = ArtService(FakeSecrets(), backend=OvhSdxlBackend("secret", client))
        art = await service.generate("pose lateral", preset="full_body", remote_processing_allowed=True)
    assert art.mime_type == "image/png"
    assert str(calls[0].url) == OVH_SDXL_URL
    assert b"negative_prompt" in calls[0].content
    assert calls[0].headers["Authorization"] == "Bearer secret"


@pytest.mark.asyncio
async def test_provider_429_does_not_retry_or_expose_body():
    calls = []
    def transport(req):
        calls.append(req)
        return httpx.Response(429, text="sensitive-provider-body")
    async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
        service = ArtService(FakeSecrets(), backend=OvhSdxlBackend("secret", client))
        with pytest.raises(ArtError) as exc:
            await service.generate("desenho", remote_processing_allowed=True)
    assert str(exc.value) == "ART_PROVIDER_RATE_LIMITED"
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_art_lock_prevents_queued_duplicate_generation():
    entered = asyncio.Event()
    release = asyncio.Event()
    class SlowBackend(FakeBackend):
        async def generate(self, prompt, negative_prompt):
            entered.set()
            await release.wait()
            return PNG, "image/png"
    service = ArtService(FakeSecrets(), backend=SlowBackend())
    first = asyncio.create_task(service.generate("a", remote_processing_allowed=True))
    await entered.wait()
    with pytest.raises(ArtError) as exc:
        await service.generate("b", remote_processing_allowed=True)
    assert exc.value.code == "ART_BUSY"
    release.set()
    await first


def test_art_image_transport_validation():
    assert detect_image(PNG) == "image/png"
    for data in [b"<html>bad response</html>", b"\x89PNG\r\n\x1a\n" + b"\0" * 32, b"0" * (8 * 1024 * 1024 + 1)]:
        with pytest.raises(ArtError):
            detect_image(data)


def test_discord_art_permission_binds_to_real_profile():
    class Profiles:
        def resolve_actor(self, **kwargs):
            if kwargs["external_actor_id"] == "12345678901234567":
                return SimpleNamespace(id="PERSON_BK")
            return None
    core = SimpleNamespace(profiles=Profiles())
    req = ArtGenerateRequest(prompt="desenho", discord_actor_id="12345678901234567")
    authorize_art_request(core, "messaging", req)
    for role, actor in [("web", "12345678901234567"), ("messaging", "99999999999999999"), ("admin", "12345678901234567")]:
        with pytest.raises(HTTPException) as exc:
            authorize_art_request(core, role, ArtGenerateRequest(prompt="x", discord_actor_id=actor))
        assert exc.value.status_code == 403
