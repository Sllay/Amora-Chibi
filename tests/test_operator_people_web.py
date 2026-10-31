from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.pairing import PairingManager
from amora.time import SimulatedClock


def _cloud(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_MODE", "cloud")
    monkeypatch.setenv("AMORA_PUBLIC_ORIGINS", "https://amora.example")
    monkeypatch.setenv("AMORA_DURABLE_FILESYSTEM", "true")
    monkeypatch.setenv("AMORA_SECRET_LOCAL_API_TOKEN", "test-operator-admin-token-32-bytes-minimum")
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    client = TestClient(create_api(core), base_url="https://amora.example")
    return core, client


def _operator_headers(core: AmoraCore, client: TestClient) -> dict[str, str]:
    manager = PairingManager(
        core.paths.cache / "operator-pairing.json",
        core.clock,
        ttl_seconds=180,
        max_attempts=5,
    )
    response = client.post(
        "/v1/pairing/redeem",
        json={"code": manager.issue().code, "client": "operator"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    assert token.startswith("browser.v3.")
    return {"Authorization": "Bearer " + token}


def _web_headers(core: AmoraCore, client: TestClient) -> dict[str, str]:
    response = client.post(
        "/v1/pairing/redeem",
        json={"code": core.pairing.issue().code, "client": "web"},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": "Bearer " + response.json()["token"]}


def test_operator_pairing_is_separate_bounded_and_scoped(tmp_path: Path, monkeypatch) -> None:
    core, client = _cloud(tmp_path, monkeypatch)
    try:
        ordinary_code = core.pairing.issue().code
        assert (
            client.post(
                "/v1/pairing/redeem",
                json={"code": ordinary_code, "client": "operator"},
            ).status_code
            == 401
        )

        core.clock.advance(timedelta(seconds=61))
        web = _web_headers(core, client)
        assert client.get("/v1/operator/people", headers=web).status_code == 403

        core.clock.advance(timedelta(seconds=61))
        operator = _operator_headers(core, client)
        assert client.get("/v1/operator/people", headers=operator).status_code == 200
        assert client.get("/v1/operations/state", headers=operator).status_code == 200
        assert client.get("/v1/web/state", headers=operator).status_code == 403
        assert client.get("/v1/state", headers=operator).status_code == 403

        core.clock.advance(timedelta(hours=12, seconds=1))
        assert client.get("/v1/operator/people", headers=operator).status_code == 401
    finally:
        client.close()
        core.close()


def test_operator_profile_edit_and_seed_relation_removal_survive_restart(tmp_path: Path, monkeypatch) -> None:
    core, client = _cloud(tmp_path, monkeypatch)
    home = core.paths.home
    try:
        operator = _operator_headers(core, client)
        people = client.get("/v1/operator/people", headers=operator).json()["profiles"]
        lucas = next(item for item in people if item["entity_id"] == "PERSON_LUCAS")
        lucas.pop("binding_count", None)
        lucas["aliases"] = ["Luquinhas"]
        lucas["short_description"] = "Filho de BK e irmão da Amora."
        response = client.put(
            "/v1/operator/people/PERSON_LUCAS",
            headers=operator,
            json={"profile": lucas, "confirm_family_authority_change": False},
        )
        assert response.status_code == 200, response.text

        removed = client.delete(
            "/v1/operator/relationships",
            headers=operator,
            params={
                "source_id": "PERSON_LUCAS",
                "relation_type": "sibling_of",
                "target_id": "ENTITY_AMORA",
                "mirror": "true",
                "confirm": "true",
            },
        )
        assert removed.status_code == 200, removed.text
        assert core.identities.relationship("PERSON_LUCAS", "sibling_of", "ENTITY_AMORA") is None
    finally:
        client.close()
        core.close()

    monkeypatch.setenv("AMORA_HOME", str(home))
    again = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        profile = again.profiles.profile("PERSON_LUCAS")
        assert profile is not None
        assert profile.short_description == "Filho de BK e irmão da Amora."
        assert "Luquinhas" in profile.aliases
        assert again.identities.resolve_alias("Luquinhas").id == "PERSON_LUCAS"
        assert again.identities.relationship("PERSON_LUCAS", "sibling_of", "ENTITY_AMORA") is None
        assert again.identities.relationship("ENTITY_AMORA", "sibling_of", "PERSON_LUCAS") is None
    finally:
        again.close()


def test_family_authority_change_requires_confirmation_and_updates_runtime_gate(tmp_path: Path, monkeypatch) -> None:
    core, client = _cloud(tmp_path, monkeypatch)
    try:
        operator = _operator_headers(core, client)
        lucas = core.profiles.profile("PERSON_LUCAS").model_dump(mode="json")
        lucas["can_define_amora_family"] = True
        payload = {"profile": lucas, "confirm_family_authority_change": False}
        denied = client.put("/v1/operator/people/PERSON_LUCAS", headers=operator, json=payload)
        assert denied.status_code == 409
        learner = core.cognition.compiler.relationship_learning
        assert "PERSON_LUCAS" not in learner.family_authority_ids

        payload["confirm_family_authority_change"] = True
        accepted = client.put("/v1/operator/people/PERSON_LUCAS", headers=operator, json=payload)
        assert accepted.status_code == 200, accepted.text
        assert "PERSON_LUCAS" in core.profiles.family_authorities()
        assert "PERSON_LUCAS" in learner.family_authority_ids
    finally:
        client.close()
        core.close()


def test_private_bindings_are_masked_and_separate_from_public_export(tmp_path: Path, monkeypatch) -> None:
    core, client = _cloud(tmp_path, monkeypatch)
    private_id = "+5581998765432"
    try:
        operator = _operator_headers(core, client)
        response = client.post(
            "/v1/operator/people/PERSON_BK/bindings",
            headers=operator,
            json={
                "platform": "whatsapp",
                "identifier_type": "phone_e164",
                "external_actor_id": private_id,
                "entity_id": "PERSON_BK",
                "account_scope": "personal-1",
                "trust": "operator_verified",
                "confidence": 1.0,
                "enabled": True,
            },
        )
        assert response.status_code == 200, response.text
        assert private_id not in response.text
        binding_ref = response.json()["ref"]

        listed = client.get("/v1/operator/people/PERSON_BK/bindings", headers=operator)
        assert listed.status_code == 200
        assert private_id not in listed.text
        assert listed.json()["bindings"][0]["masked_external_actor_id"].endswith("5432")

        revealed = client.get(
            f"/v1/operator/people/PERSON_BK/bindings/{binding_ref}",
            headers=operator,
        )
        assert revealed.status_code == 200
        assert revealed.json()["external_actor_id"] == private_id

        public_export = client.get("/v1/operator/people/export", headers=operator)
        assert public_export.status_code == 200
        assert private_id not in public_export.text
        private_export = client.get("/v1/operator/bindings/export", headers=operator)
        assert private_export.status_code == 200
        assert private_id in private_export.text
        assert private_id not in str(core.health_report())
    finally:
        client.close()
        core.close()


def test_people_import_validation_prevents_partial_alias_collision(tmp_path: Path, monkeypatch) -> None:
    core, client = _cloud(tmp_path, monkeypatch)
    try:
        operator = _operator_headers(core, client)
        exported = client.get("/v1/operator/people/export", headers=operator).json()
        lucas = next(item for item in exported["profiles"] if item["entity_id"] == "PERSON_LUCAS")
        original_description = lucas["short_description"]
        lucas["aliases"] = ["BK"]
        lucas["short_description"] = "isso nunca deve ser aplicado"

        preview = client.post(
            "/v1/operator/people/import/preview",
            headers=operator,
            json={
                "catalog": exported,
                "replace_family_relationships": True,
                "confirm": False,
            },
        )
        assert preview.status_code == 422

        apply = client.post(
            "/v1/operator/people/import/apply",
            headers=operator,
            json={
                "catalog": exported,
                "replace_family_relationships": True,
                "confirm": True,
            },
        )
        assert apply.status_code == 422
        assert core.profiles.profile("PERSON_LUCAS").short_description == original_description
        assert core.identities.resolve_alias("BK").id == "PERSON_BK"
    finally:
        client.close()
        core.close()


def test_people_import_is_confirmed_and_preserves_relationship_revision_history(tmp_path: Path, monkeypatch) -> None:
    core, client = _cloud(tmp_path, monkeypatch)
    try:
        operator = _operator_headers(core, client)
        exported = client.get("/v1/operator/people/export", headers=operator).json()
        relation = next(
            item
            for item in exported["relationships"]
            if item["source"] == "PERSON_BK"
            and item["relation_type"] == "father_of"
            and item["target"] == "PERSON_LUCAS"
        )
        relation["confidence"] = 0.91
        exported["revision"] = "test-import-revision"
        core.clock.advance(timedelta(seconds=1))

        preview = client.post(
            "/v1/operator/people/import/preview",
            headers=operator,
            json={"catalog": exported, "replace_family_relationships": True},
        )
        assert preview.status_code == 200, preview.text
        assert ["PERSON_BK", "father_of", "PERSON_LUCAS"] in preview.json()["relationship_change"]

        denied = client.post(
            "/v1/operator/people/import/apply",
            headers=operator,
            json={"catalog": exported, "replace_family_relationships": True, "confirm": False},
        )
        assert denied.status_code == 409

        applied = client.post(
            "/v1/operator/people/import/apply",
            headers=operator,
            json={"catalog": exported, "replace_family_relationships": True, "confirm": True},
        )
        assert applied.status_code == 200, applied.text
        assert ["PERSON_BK", "father_of", "PERSON_LUCAS"] in applied.json()["diff"]["relationship_change"]
        history = [
            item
            for item in core.relationships.history("PERSON_BK", target_id="PERSON_LUCAS")
            if item.relation_type == "father_of"
        ]
        assert len(history) >= 2
        assert history[0].confidence == 0.91
        assert history[0].supersedes_id == history[1].id
        assert history[1].valid_until == history[0].valid_from
    finally:
        client.close()
        core.close()


def test_web_shell_exposes_people_and_console_without_avatar_body_work(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        with TestClient(create_api(core)) as client:
            page = client.get("/web/")
            assert page.status_code == 200
            assert "Pessoas" in page.text
            assert "Console" in page.text
            assert "amora pair --role operator" in page.text
            assert "operator-panel.js" in page.text
            assert "PESSOAS & FAMÍLIA" in page.text
    finally:
        core.close()
