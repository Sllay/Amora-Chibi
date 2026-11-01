from __future__ import annotations

import json
from pathlib import Path

from amora.app import AmoraCore
from amora.identity import PlatformKind


def _write_bindings(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "bindings": [
                    {
                        "platform": "whatsapp",
                        "identifier_type": "phone_e164",
                        "external_actor_id": "5581999990001",
                        "entity_id": "PERSON_BK",
                        "account_scope": "amora-primary",
                        "trust": "operator_verified",
                        "confidence": 1.0,
                        "enabled": True,
                    },
                    {
                        "platform": "whatsapp",
                        "identifier_type": "phone_e164",
                        "external_actor_id": "5581999990002",
                        "entity_id": "PERSON_BK",
                        "account_scope": "amora-primary",
                        "trust": "operator_verified",
                        "confidence": 1.0,
                        "enabled": True,
                    },
                    {
                        "platform": "discord",
                        "identifier_type": "user_id",
                        "external_actor_id": "123456789012345678",
                        "entity_id": "PERSON_LUCAS",
                        "account_scope": "primary",
                        "trust": "operator_verified",
                        "confidence": 1.0,
                        "enabled": True,
                    },
                ],
            }
        ),
        encoding="utf-8",
    )


def test_private_bindings_map_multiple_accounts_to_one_person_and_survive_restart(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / ".amora"
    bindings = home / "secrets" / "identity_bindings.json"
    _write_bindings(bindings)
    monkeypatch.setenv("AMORA_HOME", str(home))

    first = AmoraCore.bootstrap()
    try:
        assert first.profiles.resolve_actor(
            platform=PlatformKind.WHATSAPP,
            identifier_type="phone_e164",
            external_actor_id="5581999990001",
            account_scope="amora-primary",
        ).id == "PERSON_BK"
        assert first.profiles.resolve_actor(
            platform="whatsapp",
            identifier_type="phone_e164",
            external_actor_id="5581999990002",
            account_scope="amora-primary",
        ).id == "PERSON_BK"
        assert first.profiles.resolve_actor(
            platform="discord",
            identifier_type="user_id",
            external_actor_id="123456789012345678",
        ).id == "PERSON_LUCAS"
        assert first.profiles.resolve_actor(
            platform="discord",
            identifier_type="discord_user_id",
            external_actor_id="123456789012345678",
        ).id == "PERSON_LUCAS"
        assert len(first.profiles.bindings_for("PERSON_BK")) == 2
        report_text = json.dumps(first.health_report(), ensure_ascii=False)
        assert "5581999990001" not in report_text
        assert "123456789012345678" not in report_text
    finally:
        first.close()

    bindings.unlink()
    second = AmoraCore.bootstrap()
    try:
        assert second.profiles.resolve_actor(
            platform="whatsapp",
            identifier_type="phone_e164",
            external_actor_id="5581999990002",
            account_scope="amora-primary",
        ).id == "PERSON_BK"
        assert second.profiles.resolve_actor(
            platform="discord",
            identifier_type="discord_user_id",
            external_actor_id="123456789012345678",
        ).id == "PERSON_LUCAS"
        assert second.health_report()["platform_identity_binding_count"] == 3
    finally:
        second.close()


def test_public_profile_catalog_does_not_contain_private_platform_ids() -> None:
    raw = Path("config/people_profiles.json").read_text(encoding="utf-8")
    assert "phone_e164" not in raw
    assert "discord" not in raw.casefold()
    assert "whatsapp" not in raw.casefold()
