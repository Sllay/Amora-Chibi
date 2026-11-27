from __future__ import annotations

from pathlib import Path
import os
import logging
from typing import Callable

from fastapi import Depends, FastAPI, HTTPException, Request, Response, status
from fastapi.staticfiles import StaticFiles

from amora.web import register_web_routes

from .bundle import MAX_AVATAR_BUNDLE_BYTES, build_avatar_bundle, restore_avatar_bundle
from .models import AvatarProject


MAX_AVATAR_ASSET_BYTES = 12 * 1024 * 1024


def register_avatar_routes(app: FastAPI, core: object, require_auth: Callable[..., None]) -> None:
    from .visual_stream import register_visual_stream
    register_visual_stream(app, core)
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

    @app.get("/v1/avatar/bundle", dependencies=[Depends(require_auth)])
    def export_avatar_bundle() -> Response:
        try:
            body = build_avatar_bundle(avatar.store, avatar.project())
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        return Response(
            content=body,
            media_type="application/zip",
            headers={
                "Cache-Control": "private, no-store",
                "Content-Disposition": 'attachment; filename="amora_avatar.zip"',
            },
        )

    @app.put("/v1/avatar/bundle", dependencies=[Depends(require_auth)])
    async def import_avatar_bundle(request: Request) -> dict[str, object]:
        body = await request.body()
        if len(body) > MAX_AVATAR_BUNDLE_BYTES:
            raise HTTPException(
                status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                detail="avatar portable bundle is too large",
            )
        try:
            result = restore_avatar_bundle(avatar.store, body)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
        return {
            "project": result.project.model_dump(mode="json"),
            "assets_written": result.assets_written,
            "assets_unchanged": result.assets_unchanged,
        }

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
    # Isolated, opt-in staging mount. Never replace the legacy avatar or V4.5.
    # Release packaging and license acceptance are a separate gate.
    v5_root = os.environ.get("AMORA_VISUAL_V5_STAGING_ROOT")
    # An optional preview failure must never stop the Core or expose unverified files.
    app.state.visual_preview_status = "disabled"
    if v5_root:
        reason = "bundle_invalid"
        try:
            if len(os.environ.get("AMORA_VISUAL_V5_PREVIEW_PASSWORD", "").encode("utf-8")) < 32:
                reason = "password_invalid"
                raise RuntimeError("V5 preview password invalid")
            root = Path(v5_root).resolve()
            from .visual_preview import verify_preview
            if not (root / "manifest.json").is_file():
                reason = "manifest_missing"
            verify_preview(root)
            app.mount("/avatar/v5", StaticFiles(directory=str(root), html=True), name="visual-v5-staging")
        except (RuntimeError, OSError, ValueError):
            app.state.visual_preview_status = "unavailable"
            logging.getLogger(__name__).error("VISUAL_V5_PREVIEW_DISABLED reason=%s; Core and legacy avatar remain available", reason)
        else:
            from amora.security import issue_browser_token
            app.state.visual_preview_root = root
            app.state.visual_preview_issue = lambda ttl: issue_browser_token(core.secrets.get("LOCAL_API_TOKEN"), core.clock, "visual", visual_ttl=ttl)
            app.state.visual_preview_status = "ready"
            logging.getLogger(__name__).info("VISUAL_V5_PREVIEW_READY")
    if web_root.is_dir():
        app.mount("/avatar", StaticFiles(directory=str(web_root), html=True), name="avatar")

    # New independent, read-only expression laboratory. Serve it through the
    # existing Northflank Core and port, without starting a third service or
    # replacing /avatar (the established V0.9 Avatar Studio).
    # Source is a single self-contained HTML with embedded user artwork:
    # no Python image generation, Chromium, tokens or external dependencies.
    visual_root = Path(
        os.environ.get("AMORA_WEB_ROOT", str(Path(__file__).resolve().parents[3] / "web"))
    ) / "visual-engine"
    if visual_root.is_dir():
        app.mount(
            "/visual-engine",
            StaticFiles(directory=str(visual_root), html=True),
            name="visual-engine",
        )

    # Browser surfaces share Core contracts but remain separate platform adapters.
    # Keep the assembly near the existing static-surface hook until API composition
    # is split into a dedicated platform registry.
    register_web_routes(app, core, require_auth)
