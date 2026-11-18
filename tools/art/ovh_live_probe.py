#!/usr/bin/env python3
"""One-shot OVH SDXL live probe, independent from Amora's Core/database/Discord.

Uses OVH's documented anonymous limit (2 requests/minute/IP/model) by default.
To test authenticated mode locally, set OVH_AI_ENDPOINTS_ACCESS_TOKEN in the
environment. No token is read from or written to any persistent Amora secrets.

Usage:
  python tools/art/ovh_live_probe.py --output reports/art-probe
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import time
from pathlib import Path

from amora.art.service import ArtError, ArtService, OvhSdxlBackend, STYLE_VERSION

# Completely synthetic prompt: no Amora appearance data or user images.
PROMPT = (
    "An original friendly tiny woodland creature with a soft oval body, "
    "large expressive eyes, rounded paws and little leaf-shaped ears; "
    "hand-drawn cartoon illustration, gentle ink linework, warm pastel "
    "colors, clean off-white background, full character visible"
)
MIME_EXTENSIONS = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
}


class ProbeSecrets:
    def get(self, name: str) -> str | None:
        if name == "OVH_AI_ENDPOINTS_ACCESS_TOKEN":
            # Local manual opt-in only; GitHub anonymous CI does not supply it.
            return os.getenv("OVH_AI_ENDPOINTS_ACCESS_TOKEN") or None
        return None


async def probe(out: Path) -> dict[str, object]:
    started = time.monotonic()
    token = ProbeSecrets().get("OVH_AI_ENDPOINTS_ACCESS_TOKEN")
    result = {
        "status": "unavailable",
        "provider": "ovh-sdxl",
        "auth_mode": "authenticated" if token else "anonymous",
        "style_version": STYLE_VERSION,
        "image_file": None,
        "content_sha256": None,
        "error_code": None,
    }
    try:
        # Use precisely the production adapter, without a running AmoraCore.
        service = ArtService(ProbeSecrets(), backend=OvhSdxlBackend(token))
        generated = await service.generate(
            PROMPT,
            preset="full_body",
            remote_processing_allowed=True,
        )
        # Reject HTTP 200 pages / empty placeholders before producing an artifact.
        if len(generated.image) < 8192:
            raise ArtError("ART_IMAGE_SUSPICIOUSLY_SMALL")
        suffix = MIME_EXTENSIONS.get(generated.mime_type)
        if suffix is None:
            raise ArtError("ART_IMAGE_MIME_INVALID")
        out.mkdir(parents=True, exist_ok=True)
        image_path = out / ("ovh_sdxl_live" + suffix)
        image_path.write_bytes(generated.image)
        result.update(
            status="ok",
            image_file=image_path.name,
            bytes=len(generated.image),
            content_sha256=hashlib.sha256(generated.image).hexdigest(),
            prompt_sha256=generated.prompt_sha256,
        )
    except ArtError as exc:
        # Never include HTTP bodies, prompts, full URL with query or secrets.
        result["error_code"] = exc.code
    except Exception:
        result["error_code"] = "ART_PROBE_UNEXPECTED_ERROR"
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    out.mkdir(parents=True, exist_ok=True)
    (out / "probe-result.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("reports/art-probe"))
    args = parser.parse_args()
    result = asyncio.run(probe(args.output))
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
