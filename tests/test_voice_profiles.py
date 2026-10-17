from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.voice.profiles import VoiceProfileCatalogStore


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap()


def test_default_voice_profile_catalog_is_valid_and_not_canonical() -> None:
    catalog, etag = VoiceProfileCatalogStore().load()

    assert catalog.schema_version == 1
    assert catalog.backend.id == "supertonic-3-int8"
    assert catalog.backend.language == "pt"
    assert catalog.lab.canonical is False
    assert catalog.lab.default_profile_id in {profile.id for profile in catalog.profiles}
    assert len(catalog.profiles) >= 4
    assert etag.startswith('"') and etag.endswith('"')


def test_voice_profile_api_requires_auth_and_supports_conditional_refresh(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client = TestClient(create_api(core))
        assert client.get("/v1/voice/profiles").status_code == 401

        token = core.secrets.get("LOCAL_API_TOKEN")
        headers = {"Authorization": f"Bearer {token}"}
        first = client.get("/v1/voice/profiles", headers=headers)
        assert first.status_code == 200
        assert first.headers["cache-control"] == "no-cache"
        assert first.headers["x-amora-voice-revision"] == first.json()["revision"]
        etag = first.headers["etag"]

        second = client.get(
            "/v1/voice/profiles",
            headers={**headers, "If-None-Match": etag},
        )
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers["etag"] == etag
    finally:
        core.close()
