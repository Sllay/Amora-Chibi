from __future__ import annotations

from pathlib import Path
import os
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.staticfiles import StaticFiles

from amora.web.routes import register_web_routes

from .models import AvatarProject


MAX_AVATAR_ASSET_BYTES = 12 * 1024 * 1024


def register_avatar_routes(app: FastAPI, core: object, require_auth: Callable[..., None]) -> None:
    avatar = core.avatar

    @app.get("/v1/avatar/state", dependencies=[Depends(require_auth)])
    def avatar_state() -> dict[str, object]:
        return avatar.semantic_state().model_dump(mode="json")

    @app.get("/v1/avatar/project", dependencies=[Depends(require_auth)])
    def avatar_project() -> dict[str, object]:
        return avatar.project().model_dump(mode="json")

    @app.put("/v1/avatar/project", dependencies=[Depends(require_auth)])
    def save_avatar_project(project: AvatarProject) -> dict[str, object]:
        return avatar.save_project(project).model_dump(mode="json")

    @app.get("/v1/avatar/assets", dependencies=[Depends(require_auth)])
    def list_avatar_assets() -> list[dict[str, object]]:
        return [item.model_dump(mode="json") for item in avatar.store.list_assets()]

    @app.put("/v1/avatar/assets/{asset_id}", dependencies=[Depends(require_auth)])
    async def put_avatar_asset(asset_id: str, request: Request) -> dict[str, object]:
        body = await request.body()
        if len(body) > MAX_AVATAR_ASSET_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="avatar asset too large",
            )
        try:
            info = avatar.store.put_asset(asset_id, request.headers.get("content-type", ""), body)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return info.model_dump(mode="json")

    @app.get("/v1/avatar/assets/{asset_id}", dependencies=[Depends(require_auth)])
    def get_avatar_asset(asset_id: str) -> Response:
        result = avatar.store.read_asset(asset_id)
        if result is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="avatar asset not found")
        info, body = result
        return Response(
            content=body,
            media_type=info.content_type,
            headers={"Cache-Control": "private, max-age=60", "ETag": info.sha256},
        )

    @app.delete("/v1/avatar/assets/{asset_id}", dependencies=[Depends(require_auth)])
    def delete_avatar_asset(asset_id: str) -> dict[str, bool]:
        return {"deleted": avatar.store.delete_asset(asset_id)}

    web_root = Path(os.environ.get("AMORA_AVATAR_ROOT", str(Path(__file__).resolve().parents[3] / "avatar")))
    if web_root.is_dir():
        app.mount("/avatar", StaticFiles(directory=str(web_root), html=True), name="avatar")

    # Browser surfaces share Core contracts but remain separate platform adapters.
    # Keep the assembly near the existing static-surface hook until API composition
    # is split into a dedicated platform registry.
    register_web_routes(app, core, require_auth)
