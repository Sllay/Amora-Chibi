from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.operator_auth import issue_operator_token
from amora.security import issue_browser_token
from amora.time import SimulatedClock


ADMIN = "test-dev-master-admin-token-32-bytes-minimum"
MASTER = "bk-personal-dev-master-key-32-bytes-minimum"
ROTATED = "bk-personal-dev-master-key-rotated-32-bytes"


def _core(tmp_path: Path, monkeypatch, *, master: str = MASTER):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_MODE", "cloud")
    monkeypatch.setenv("AMORA_PUBLIC_ORIGINS", "https://amora.example")
    monkeypatch.setenv("AMORA_DURABLE_FILESYSTEM", "true")
    monkeypatch.setenv("AMORA_SECRET_LOCAL_API_TOKEN", ADMIN)
    monkeypatch.setenv("AMORA_DEV_MASTER_KEY", master)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    client = TestClient(create_api(core), base_url="https://amora.example")
    return core, client


def test_dev_master_signed_web_and_operator_credentials_are_scoped(tmp_path: Path, monkeypatch) -> None:
    core, client = _core(tmp_path, monkeypatch)
    try:
        web_token = issue_browser_token(MASTER, core.clock, "web")
        web = {"Authorization": "Bearer " + web_token}
        assert client.get("/v1/web/state", headers=web).status_code == 200
        assert client.get("/v1/operator/people", headers=web).status_code == 403

        operator_token = issue_operator_token(MASTER, core.clock)
        operator = {"Authorization": "Bearer " + operator_token}
        assert client.get("/v1/operator/people", headers=operator).status_code == 200
        assert client.get("/v1/operations/state", headers=operator).status_code == 200
        assert client.get("/v1/web/state", headers=operator).status_code == 403

        wrong = {"Authorization": "Bearer " + issue_operator_token("x" * 40, core.clock)}
        assert client.get("/v1/operator/people", headers=wrong).status_code == 401
    finally:
        client.close()
        core.close()


def test_rotating_dev_master_key_revokes_only_dev_signed_credentials(tmp_path: Path, monkeypatch) -> None:
    core, client = _core(tmp_path, monkeypatch)
    try:
        dev_operator = {"Authorization": "Bearer " + issue_operator_token(MASTER, core.clock)}
        admin_signed_web = {"Authorization": "Bearer " + issue_browser_token(ADMIN, core.clock, "web")}
        assert client.get("/v1/operator/people", headers=dev_operator).status_code == 200
        assert client.get("/v1/web/state", headers=admin_signed_web).status_code == 200

        monkeypatch.setenv("AMORA_DEV_MASTER_KEY", ROTATED)
        assert client.get("/v1/operator/people", headers=dev_operator).status_code == 401
        assert client.get("/v1/web/state", headers=admin_signed_web).status_code == 200

        rotated = {"Authorization": "Bearer " + issue_operator_token(ROTATED, core.clock)}
        assert client.get("/v1/operator/people", headers=rotated).status_code == 200
    finally:
        client.close()
        core.close()


def test_existing_pairing_flow_remains_available(tmp_path: Path, monkeypatch) -> None:
    core, client = _core(tmp_path, monkeypatch)
    try:
        response = client.post(
            "/v1/pairing/redeem",
            json={"code": core.pairing.issue().code, "client": "web"},
        )
        assert response.status_code == 200, response.text
        token = response.json()["token"]
        assert token.startswith("browser.v2.")
        assert client.get(
            "/v1/web/state",
            headers={"Authorization": "Bearer " + token},
        ).status_code == 200
    finally:
        client.close()
        core.close()


@pytest.mark.parametrize("master", ["too-short", ADMIN])
def test_invalid_master_key_configuration_fails_closed(tmp_path: Path, monkeypatch, master: str) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / master.replace("/", "_")))
    monkeypatch.setenv("AMORA_MODE", "cloud")
    monkeypatch.setenv("AMORA_PUBLIC_ORIGINS", "https://amora.example")
    monkeypatch.setenv("AMORA_DURABLE_FILESYSTEM", "true")
    monkeypatch.setenv("AMORA_SECRET_LOCAL_API_TOKEN", ADMIN)
    monkeypatch.setenv("AMORA_DEV_MASTER_KEY", master)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        with pytest.raises(ValueError):
            create_api(core)
    finally:
        core.close()
