from __future__ import annotations

import asyncio
import hashlib
import os
from dataclasses import dataclass
from typing import Protocol

import httpx

# OVHcloud's documented dedicated text-to-image API. It accepts prompt and
# negative_prompt, but does not document img2img, arbitrary LoRA or seed input.
OVH_SDXL_URL = "https://stable-diffusion-xl.endpoints.kepler.ai.cloud.ovh.net/api/text2image"
STYLE_VERSION = "amora-cartoon-handdrawn-v1"
MAX_IMAGE_BYTES = 8 * 1024 * 1024
MAX_PROMPT_BYTES = 4096

STYLE = (
    "Original stylized hand-drawn cartoon illustration, charming readable chibi "
    "proportions, graceful organic line art, subtle natural pen variation, gentle "
    "brush-painted color transitions, soft restrained cel shading, clean silhouette, "
    "deliberate shapes, polished 2D character art, visually coherent craftsmanship"
)
NEGATIVE = (
    "photorealism, 3d render, plastic skin, metallic shine, excessive gloss, "
    "overcomplicated details, noisy textures, broken anatomy, extra fingers, "
    "extra limbs, duplicate features, illegible outlines, text, watermark"
)
PRESETS = {
    "portrait": "close-up character portrait, uncluttered background, expressive face",
    "full_body": "full-body character illustration, visible hands and feet, neutral pose",
    "sticker": "single expressive sticker character, simple readable silhouette, plain background",
    "scene": "coherent illustrated scene with clear composition and consistent character art",
    "expression": "single character expression illustration, face and gesture readable",
}


class ArtError(Exception):
    """Stable error code. Never include provider body or credentials in logs."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass(frozen=True)
class Artwork:
    image: bytes
    mime_type: str
    provider: str
    style_version: str
    prompt_sha256: str


class ArtBackend(Protocol):
    name: str
    supports_references: bool
    supports_lora: bool

    async def generate(self, prompt: str, negative_prompt: str) -> tuple[bytes, str]: ...


def detect_image(data: bytes) -> str:
    """Reject obvious non-images, oversized payloads and invalid PNG geometry.

    This is transport integrity validation, not a complete image decoder.
    """
    if not (32 <= len(data) <= MAX_IMAGE_BYTES):
        raise ArtError("ART_IMAGE_SIZE_INVALID")
    if data.startswith(b"\x89PNG\r\n\x1a\n") and data[12:16] == b"IHDR":
        width = int.from_bytes(data[16:20], "big")
        height = int.from_bytes(data[20:24], "big")
        if 0 < width <= 4096 and 0 < height <= 4096:
            return "image/png"
    if data.startswith(b"\xff\xd8\xff") and data.endswith(b"\xff\xd9"):
        return "image/jpeg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    raise ArtError("ART_IMAGE_CONTENT_INVALID")


class OvhSdxlBackend:
    name = "ovh-sdxl"
    supports_references = False
    supports_lora = False

    def __init__(self, token: str | None = None, client: httpx.AsyncClient | None = None) -> None:
        # OVH documents a small anonymous pool (2 req/min/IP/model).
        # Never emit a literal "Bearer None" or assume a key is required.
        self._token = token or ""
        self._client = client

    async def generate(self, prompt: str, negative_prompt: str) -> tuple[bytes, str]:
        async def call(client: httpx.AsyncClient) -> bytes:
            try:
                async with client.stream(
                    "POST", OVH_SDXL_URL,
                    json={"prompt": prompt, "negative_prompt": negative_prompt},
                    headers={
                        **({"Authorization": f"Bearer {self._token}"} if self._token else {}),
                        "Accept": "application/octet-stream",
                        "Content-Type": "application/json",
                    },
                ) as response:
                    if response.status_code == 429:
                        raise ArtError("ART_PROVIDER_RATE_LIMITED")
                    if response.status_code in {401, 403}:
                        raise ArtError("ART_PROVIDER_AUTH_FAILED")
                    if response.status_code != 200:
                        raise ArtError("ART_PROVIDER_UNAVAILABLE")
                    chunks = []
                    total = 0
                    async for chunk in response.aiter_bytes():
                        total += len(chunk)
                        if total > MAX_IMAGE_BYTES:
                            raise ArtError("ART_IMAGE_SIZE_INVALID")
                        chunks.append(chunk)
                    return b"".join(chunks)
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                raise ArtError("ART_PROVIDER_NETWORK_ERROR") from exc

        if self._client is None:
            async with httpx.AsyncClient(timeout=httpx.Timeout(65.0, connect=8.0), follow_redirects=False) as client:
                image = await call(client)
        else:
            image = await call(self._client)
        return image, detect_image(image)


class ArtService:
    """Canonical style & policy stay in Core, remote model only renders pixels.

    V1 supports text-to-image only. References and LoRAs must never be silently
    dropped, because doing so would misrepresent identity consistency.
    """

    def __init__(self, secret_store, *, backend: ArtBackend | None = None) -> None:
        self._secrets = secret_store
        self._backend = backend
        self._lock = asyncio.Lock()

    def capabilities(self) -> dict[str, object]:
        return {
            "style_version": STYLE_VERSION,
            "presets": list(PRESETS),
            "provider": self._backend.name if self._backend else "ovh-sdxl",
            "text_to_image": True,
            "image_references": False,
            "custom_lora": False,
            "seed_control": False,
            "training": False,
        }

    async def generate(
        self, prompt: str, *,
        preset: str = "sticker",
        remote_processing_allowed: bool = False,
        reference_ids: tuple[str, ...] = (),
        lora_id: str | None = None,
    ) -> Artwork:
        if not remote_processing_allowed:
            raise ArtError("ART_REMOTE_PROCESSING_NOT_ALLOWED")
        if reference_ids:
            raise ArtError("ART_REFERENCES_UNSUPPORTED")
        if lora_id:
            raise ArtError("ART_LORA_UNSUPPORTED")
        if preset not in PRESETS:
            raise ArtError("ART_PRESET_INVALID")
        prompt = prompt.strip()
        if not prompt or len(prompt.encode("utf-8")) > MAX_PROMPT_BYTES:
            raise ArtError("ART_PROMPT_INVALID")
        # No auto-generated prompt about Amora's exact facial or clothing traits:
        # those need confirmation against the owner's canonical image sheets.
        composed = f"{STYLE}. {PRESETS[preset]}. User request: {prompt}."
        if self._lock.locked():
            raise ArtError("ART_BUSY")
        async with self._lock:
            provider = self._backend
            if provider is None:
                # Secrets are read only at execution time, never copied to
                # persistent state, prompts, audit records or exception messages.
                provider = OvhSdxlBackend(self._secrets.get("OVH_AI_ENDPOINTS_ACCESS_TOKEN") or "")
            image, mime = await provider.generate(composed, NEGATIVE)
            if detect_image(image) != mime:
                raise ArtError("ART_IMAGE_CONTENT_INVALID")
            return Artwork(
                image=image,
                mime_type=mime,
                provider=provider.name,
                style_version=STYLE_VERSION,
                prompt_sha256=hashlib.sha256(composed.encode("utf-8")).hexdigest(),
            )
