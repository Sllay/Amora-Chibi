from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from amora.api import create_api
from amora.app import AmoraCore
from amora.voice.profile import AmoraVoiceProfile, PROFILE_PATH, load_voice_profile


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap()


def test_canonical_profile_is_supertonic_speaker_zero_and_versioned() -> None:
    profile, etag, canonical = load_voice_profile()
    assert profile.profile_id == "amora-voice-v1"
    assert profile.revision >= 1
    assert profile.base.backend == "supertonic3-int8"
    assert profile.base.speaker_id == 0
    assert profile.base.language == "pt"
    assert profile.active_profile == "NATURAL"
    assert len(etag) == 64
    assert json.loads(canonical)["signature"]["fantasy_texture"] > 0


def test_profile_rejects_server_attempt_to_silently_swap_base_speaker(tmp_path: Path) -> None:
    payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    payload["base"]["speaker_id"] = 1
    target = tmp_path / "invalid-profile.json"
    target.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(ValidationError):
        load_voice_profile(target)


def test_expression_profile_must_reference_configured_profile() -> None:
    payload = json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    payload["active_profile"] = "DOES_NOT_EXIST"
    with pytest.raises(ValidationError):
        AmoraVoiceProfile.model_validate(payload)


def test_voice_profile_endpoint_is_authenticated_and_etag_revalidates(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client = TestClient(create_api(core))
        unauthorized = client.get("/v1/voice/profile")
        assert unauthorized.status_code in {401, 403}

        token = core.secrets.get("LOCAL_API_TOKEN")
        headers = {"Authorization": f"Bearer {token}"}
        first = client.get("/v1/voice/profile", headers=headers)
        assert first.status_code == 200
        assert first.json()["base"]["speaker_id"] == 0
        assert first.headers["x-amora-voice-profile"] == "amora-voice-v1"
        etag = first.headers["etag"]

        second = client.get(
            "/v1/voice/profile",
            headers={**headers, "If-None-Match": etag},
        )
        assert second.status_code == 304
        assert second.content == b""
        assert second.headers["etag"] == etag
    finally:
        core.close()


def test_voice_state_reports_local_canonical_identity(tmp_path: Path, monkeypatch) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        token = core.secrets.get("LOCAL_API_TOKEN")
        response = TestClient(create_api(core)).get(
            "/v1/voice/state",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 200
        body = response.json()
        assert body["canonical_voice_profile"] == "amora-voice-v1"
        assert body["canonical_voice_backend"] == "supertonic3-int8"
        assert body["canonical_voice_speaker"] == 0
    finally:
        core.close()
