from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from amora.api import create_api
from amora.app import AmoraCore
from amora.voice.profiles import VoiceDspProfile, VoiceProfileCatalogStore


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
    assert len(catalog.profiles) >= 5
    virtual = next(profile for profile in catalog.profiles if profile.id == "VIRTUAL_SINGER")
    assert virtual.dsp.pitch_semitones > 0
    assert virtual.dsp.digital_mix > 0
    assert virtual.dsp.bit_depth < 16
    assert etag.startswith('"') and etag.endswith('"')


def test_dsp_profile_rejects_parameters_outside_safe_bounds() -> None:
    with pytest.raises(ValidationError):
        VoiceDspProfile(pitch_semitones=12.0)
    with pytest.raises(ValidationError):
        VoiceDspProfile(digital_mix=1.0)
    with pytest.raises(ValidationError):
        VoiceDspProfile(ring_mix=0.5)


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
