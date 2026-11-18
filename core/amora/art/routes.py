from __future__ import annotations

import base64
import os
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from .service import ArtError, ArtService, PRESETS


class ArtGenerateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    prompt: str = Field(min_length=1, max_length=1500)
    preset: str = "sticker"
    remote_processing_allowed: bool = False
    reference_ids: list[str] = Field(default_factory=list, max_length=6)
    lora_id: str | None = None
    # Only authenticated messaging workers may submit Discord actor IDs.
    discord_actor_id: str | None = Field(default=None, pattern=r"^[0-9]{10,24}$")
    account_scope: str = Field(default="primary", min_length=1, max_length=96)


def authorize_art_request(core, role: str | None, request: ArtGenerateRequest) -> None:
    if role not in {"admin", "operator", "messaging"}:
        raise HTTPException(403, detail="ART_ROLE_DENIED")
    if role == "messaging":
        if request.discord_actor_id is None:
            raise HTTPException(403, detail="ART_ACTOR_MISSING")
        actor = core.profiles.resolve_actor(
            platform="discord",
            identifier_type="discord_user_id",
            external_actor_id=request.discord_actor_id,
            account_scope=request.account_scope,
        )
        if actor is None or actor.id != "PERSON_BK":
            raise HTTPException(403, detail="ART_ACTOR_DENIED")
    elif request.discord_actor_id is not None:
        raise HTTPException(403, detail="ART_ACTOR_SPOOFED")


def register_art_routes(app: FastAPI, core, require_auth: Callable) -> None:
    service = ArtService(core.secrets)
    app.state.art_service = service
    auth = [Depends(require_auth)]

    @app.get("/v1/art/capabilities", dependencies=auth)
    def art_capabilities() -> dict[str, object]:
        return {"enabled": os.getenv("AMORA_ART_ENABLED") == "1", **service.capabilities()}

    @app.post("/v1/art/generate", dependencies=auth)
    async def art_generate(payload: ArtGenerateRequest, request: Request) -> dict[str, object]:
        if os.getenv("AMORA_ART_ENABLED") != "1":
            raise HTTPException(503, detail="ART_DISABLED")
        role = getattr(request.state, "security_role", None)
        authorize_art_request(core, role, payload)
        if payload.preset not in PRESETS:
            raise HTTPException(422, detail="ART_PRESET_INVALID")
        try:
            result = await service.generate(
                payload.prompt,
                preset=payload.preset,
                remote_processing_allowed=payload.remote_processing_allowed,
                reference_ids=tuple(payload.reference_ids),
                lora_id=payload.lora_id,
            )
        except ArtError as exc:
            status = (
                403 if exc.code == "ART_REMOTE_PROCESSING_NOT_ALLOWED" else
                422 if exc.code in {"ART_PROMPT_INVALID", "ART_PRESET_INVALID", "ART_REFERENCES_UNSUPPORTED", "ART_LORA_UNSUPPORTED"} else
                429 if exc.code in {"ART_BUSY", "ART_PROVIDER_RATE_LIMITED"} else 503
            )
            raise HTTPException(status, detail=exc.code) from None
        return {
            "image_base64": base64.b64encode(result.image).decode("ascii"),
            "content_mime": result.mime_type,
            "provider": result.provider,
            "style_version": result.style_version,
            "prompt_sha256": result.prompt_sha256,
        }
