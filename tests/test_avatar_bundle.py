from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from io import BytesIO
import json
from pathlib import Path
import zipfile

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.time import SimulatedClock


def _clock() -> SimulatedClock:
    return SimulatedClock(datetime(2026, 10, 30, 23, 40, tzinfo=timezone.utc))


def _core_client(tmp_path: Path, monkeypatch) -> tuple[AmoraCore, TestClient, dict[str, str]]:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=_clock())
    token = core.secrets.get("LOCAL_API_TOKEN")
    assert token
    return core, TestClient(create_api(core)), {"Authorization": f"Bearer {token}"}


def _seed_project_with_asset(client: TestClient, headers: dict[str, str], *, asset_body: bytes) -> dict[str, object]:
    uploaded = client.put(
        "/v1/avatar/assets/test_face",
        headers={**headers, "Content-Type": "image/png"},
        content=asset_body,
    )
    assert uploaded.status_code == 200

    project = client.get("/v1/avatar/project", headers=headers).json()
    project["name"] = "Amora Bundle Fixture"
    project["metadata"]["portable_bundle"] = "round-trip"
    project["animations"][0]["duration"] = 3.25
    project["assets"].append(
        {"id": "test_face", "name": "test_face.png", "content_type": "image/png"}
    )
    project["parts"].append(
        {"id": "face", "name": "Face", "asset_id": "test_face", "layer": 10}
    )
    saved = client.put("/v1/avatar/project", headers=headers, json=project)
    assert saved.status_code == 200
    return saved.json()


def test_avatar_portable_bundle_round_trip_restores_project_and_textures(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _core_client(tmp_path, monkeypatch)
    original_asset = b"\x89PNG\r\n\x1a\nportable-bundle-original"
    try:
        original_project = _seed_project_with_asset(client, headers, asset_body=original_asset)

        exported = client.get("/v1/avatar/bundle", headers=headers)
        assert exported.status_code == 200
        assert exported.headers["content-type"].startswith("application/zip")
        assert "amora_avatar.zip" in exported.headers["content-disposition"]

        with zipfile.ZipFile(BytesIO(exported.content), "r") as archive:
            assert set(archive.namelist()) == {
                "manifest.json",
                "project.json",
                "assets/test_face.png",
            }
            manifest = json.loads(archive.read("manifest.json"))
            project = json.loads(archive.read("project.json"))
            assert manifest["format"] == "amora-avatar-bundle"
            assert manifest["version"] == 1
            assert manifest["project"]["schema_version"] == original_project["schema_version"]
            assert manifest["model"]["animations"] == len(original_project["animations"])
            assert manifest["model"]["parts"] == len(original_project["parts"])
            assert project["animations"][0]["duration"] == 3.25
            assert archive.read("assets/test_face.png") == original_asset
            asset_manifest = manifest["assets"][0]
            assert asset_manifest["sha256"] == hashlib.sha256(original_asset).hexdigest()
            assert asset_manifest["size_bytes"] == len(original_asset)

        changed_asset = b"\x89PNG\r\n\x1a\nchanged-after-export"
        changed = client.put(
            "/v1/avatar/assets/test_face",
            headers={**headers, "Content-Type": "image/png"},
            content=changed_asset,
        )
        assert changed.status_code == 200
        current = client.get("/v1/avatar/project", headers=headers).json()
        current["name"] = "Temporary changed project"
        current["animations"][0]["duration"] = 1.1
        assert client.put("/v1/avatar/project", headers=headers, json=current).status_code == 200

        restored = client.put(
            "/v1/avatar/bundle",
            headers={**headers, "Content-Type": "application/zip"},
            content=exported.content,
        )
        assert restored.status_code == 200
        assert restored.json()["assets_written"] == 1
        assert restored.json()["assets_unchanged"] == 0

        project_after = client.get("/v1/avatar/project", headers=headers).json()
        assert project_after["name"] == "Amora Bundle Fixture"
        assert project_after["metadata"]["portable_bundle"] == "round-trip"
        assert project_after["animations"][0]["duration"] == 3.25
        asset_after = client.get("/v1/avatar/assets/test_face", headers=headers)
        assert asset_after.status_code == 200
        assert asset_after.content == original_asset
    finally:
        core.close()


def test_avatar_bundle_rejects_corrupt_asset_before_replacing_current_state(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _core_client(tmp_path, monkeypatch)
    original_asset = b"\x89PNG\r\n\x1a\nvalid-export"
    try:
        _seed_project_with_asset(client, headers, asset_body=original_asset)
        exported = client.get("/v1/avatar/bundle", headers=headers)
        assert exported.status_code == 200

        current_asset = b"\x89PNG\r\n\x1a\ncurrent-safe-state"
        assert client.put(
            "/v1/avatar/assets/test_face",
            headers={**headers, "Content-Type": "image/png"},
            content=current_asset,
        ).status_code == 200
        current_project = client.get("/v1/avatar/project", headers=headers).json()
        current_project["name"] = "Current state must survive"
        assert client.put("/v1/avatar/project", headers=headers, json=current_project).status_code == 200

        source = zipfile.ZipFile(BytesIO(exported.content), "r")
        tampered_io = BytesIO()
        with source, zipfile.ZipFile(tampered_io, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for name in source.namelist():
                payload = source.read(name)
                if name == "assets/test_face.png":
                    payload = b"corrupted payload with stale manifest hash"
                target.writestr(name, payload)

        rejected = client.put(
            "/v1/avatar/bundle",
            headers={**headers, "Content-Type": "application/zip"},
            content=tampered_io.getvalue(),
        )
        assert rejected.status_code == 400
        assert "integrity mismatch" in rejected.json()["detail"]

        project_after = client.get("/v1/avatar/project", headers=headers).json()
        assert project_after["name"] == "Current state must survive"
        asset_after = client.get("/v1/avatar/assets/test_face", headers=headers)
        assert asset_after.content == current_asset
    finally:
        core.close()


def test_avatar_bundle_export_refuses_project_with_missing_texture(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _core_client(tmp_path, monkeypatch)
    try:
        project = client.get("/v1/avatar/project", headers=headers).json()
        project["assets"].append(
            {"id": "missing_body", "name": "missing_body.png", "content_type": "image/png"}
        )
        project["parts"].append(
            {"id": "body", "name": "Body", "asset_id": "missing_body", "layer": 0}
        )
        assert client.put("/v1/avatar/project", headers=headers, json=project).status_code == 200

        exported = client.get("/v1/avatar/bundle", headers=headers)
        assert exported.status_code == 409
        assert "asset is missing" in exported.json()["detail"]
    finally:
        core.close()


def test_avatar_bundle_rejects_unsafe_zip_paths(tmp_path: Path, monkeypatch) -> None:
    core, client, headers = _core_client(tmp_path, monkeypatch)
    try:
        payload = BytesIO()
        with zipfile.ZipFile(payload, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("../escape.png", b"not allowed")
            archive.writestr("manifest.json", b"{}")

        response = client.put(
            "/v1/avatar/bundle",
            headers={**headers, "Content-Type": "application/zip"},
            content=payload.getvalue(),
        )
        assert response.status_code == 400
        assert "unsafe paths" in response.json()["detail"]
    finally:
        core.close()


def test_avatar_studio_exposes_zip_backup_controls(tmp_path: Path, monkeypatch) -> None:
    core, client, _ = _core_client(tmp_path, monkeypatch)
    try:
        studio = client.get("/avatar/")
        assert studio.status_code == 200
        assert "Exportar ZIP" in studio.text
        assert "Importar ZIP/JSON" in studio.text
        assert "/v1/avatar/bundle" in studio.text
        assert 'accept="application/zip,application/x-zip-compressed,.zip,application/json,.json"' in studio.text
    finally:
        core.close()
