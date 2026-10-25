from pathlib import Path

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore


def _core(tmp_path: Path, monkeypatch) -> AmoraCore:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    return AmoraCore.bootstrap()


def test_pairing_redeems_once_and_returns_scoped_web_token_in_local_mode(
    tmp_path: Path, monkeypatch
) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        issued = core.pairing.issue()
        expected = core.secrets.get("LOCAL_API_TOKEN")
        client = TestClient(create_api(core))

        bad = client.post("/v1/pairing/redeem", json={"code": "000000"})
        assert bad.status_code == 401

        response = client.post("/v1/pairing/redeem", json={"code": issued.code})
        assert response.status_code == 200
        assert response.json()["token"] != expected
        assert response.json()["token"].startswith("browser.v2.")
        assert response.json()["token_type"] == "Bearer"
        browser = {"Authorization": "Bearer " + response.json()["token"]}
        assert client.get("/v1/web/state", headers=browser).status_code == 200
        assert client.get("/v1/state", headers=browser).status_code == 403

        replay = client.post("/v1/pairing/redeem", json={"code": issued.code})
        assert replay.status_code == 401
    finally:
        core.close()


def test_pairing_rejects_malformed_code_before_manager(
    tmp_path: Path, monkeypatch
) -> None:
    core = _core(tmp_path, monkeypatch)
    try:
        client = TestClient(create_api(core))
        assert (
            client.post("/v1/pairing/redeem", json={"code": "123"}).status_code == 422
        )
        assert (
            client.post("/v1/pairing/redeem", json={"code": "abcdef"}).status_code
            == 422
        )
    finally:
        core.close()
