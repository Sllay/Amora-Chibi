from __future__ import annotations

import httpx
from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.pairing import PairingManager
from amora.storage.remote import LibSQLBackend
from amora.time import SimulatedClock
from test_remote_storage import HranaSQLite


def operator_headers(core: AmoraCore, client: TestClient) -> dict[str, str]:
    manager = PairingManager(
        core.paths.cache / "operator-pairing.json",
        core.clock,
        ttl_seconds=180,
        max_attempts=5,
    )
    result = client.post(
        "/v1/pairing/redeem",
        json={"code": manager.issue().code, "client": "operator"},
    )
    assert result.status_code == 200, result.text
    return {"Authorization": "Bearer " + result.json()["token"]}


def test_operator_people_and_private_bindings_survive_remote_compute_replacement(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_MODE", "cloud")
    monkeypatch.setenv("AMORA_PUBLIC_ORIGINS", "https://amora.example")
    monkeypatch.setenv("AMORA_SECRET_LOCAL_API_TOKEN", "remote-operator-admin-token-32-bytes-minimum")
    monkeypatch.setenv("AMORA_ASSET_STORAGE", "database")
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "first-compute"))

    server = HranaSQLite(tmp_path / "remote.db")
    backend = LibSQLBackend(
        "https://database.example",
        "database-test-only",
        transport=httpx.MockTransport(server),
    )
    clock = SimulatedClock()
    first = AmoraCore.bootstrap(clock=clock, storage=backend)
    try:
        with TestClient(create_api(first), base_url="https://amora.example") as client:
            headers = operator_headers(first, client)
            bk = first.profiles.profile("PERSON_BK").model_dump(mode="json")
            bk["short_description"] = "Perfil editado pela Web e persistido remotamente."
            saved = client.put(
                "/v1/operator/people/PERSON_BK",
                headers=headers,
                json={"profile": bk, "confirm_family_authority_change": False},
            )
            assert saved.status_code == 200, saved.text

            binding = client.post(
                "/v1/operator/people/PERSON_BK/bindings",
                headers=headers,
                json={
                    "platform": "discord",
                    "identifier_type": "user_id",
                    "external_actor_id": "123456789012345678",
                    "entity_id": "PERSON_BK",
                    "account_scope": "alt-1",
                    "trust": "operator_verified",
                    "confidence": 1.0,
                    "enabled": True,
                },
            )
            assert binding.status_code == 200, binding.text
            assert "123456789012345678" not in binding.text
    finally:
        first.close()

    monkeypatch.setenv("AMORA_HOME", str(tmp_path / "replacement-compute"))
    second = AmoraCore.bootstrap(clock=clock, storage=backend)
    try:
        profile = second.profiles.profile("PERSON_BK")
        assert profile is not None
        assert profile.short_description == "Perfil editado pela Web e persistido remotamente."
        bindings = second.profiles.bindings_for("PERSON_BK")
        assert any(
            item.platform.value == "discord"
            and item.external_actor_id == "123456789012345678"
            and item.account_scope == "alt-1"
            for item in bindings
        )
        assert second.profiles.resolve_actor(
            platform="discord",
            identifier_type="user_id",
            external_actor_id="123456789012345678",
            account_scope="alt-1",
        ).id == "PERSON_BK"
    finally:
        second.close()
    assert not server.streams
