from __future__ import annotations

from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.identity import BindingTrust, PlatformIdentityBinding, PlatformKind
from amora.messaging import MessagingPlatform
from amora.time import SimulatedClock


def _bind(core: AmoraCore, entity_id: str, actor_id: str) -> None:
    core.profiles.upsert_binding(
        PlatformIdentityBinding(
            platform=PlatformKind.DISCORD,
            identifier_type="discord_user_id",
            external_actor_id=actor_id,
            entity_id=entity_id,
            account_scope="primary",
            trust=BindingTrust.OPERATOR_VERIFIED,
            confidence=1.0,
        )
    )


def _payload(actor_id: str, action: str) -> dict[str, object]:
    return {
        "platform": "discord",
        "account_scope": "primary",
        "action": action,
        "sender_identifiers": [
            {
                "identifier_type": "discord_user_id",
                "external_actor_id": actor_id,
                "confidence": 1.0,
            }
        ],
    }


def test_worker_presence_control_resolves_canonical_person_and_never_grants_authority(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setenv("AMORA_HOME", str(tmp_path / ".amora"))
    monkeypatch.setenv("AMORA_MESSAGING_SERVICE_TOKEN", "m" * 48)
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    try:
        _bind(core, "PERSON_BK", "111111111111111111")
        _bind(core, "PERSON_LUCAS", "222222222222222222")
        headers = {"Authorization": "Bearer " + "m" * 48}
        with TestClient(create_api(core)) as client:
            allowed = client.post(
                "/v1/messaging/worker/presence-control",
                headers=headers,
                json=_payload("111111111111111111", "connect"),
            )
            assert allowed.status_code == 200, allowed.text
            assert allowed.json()["authorized"] is True
            assert allowed.json()["executed"] is True
            assert allowed.json()["speaker_entity_id"] == "PERSON_BK"
            assert core.platform_presence.snapshot(MessagingPlatform.DISCORD).desired_connected is True

            denied = client.post(
                "/v1/messaging/worker/presence-control",
                headers=headers,
                json=_payload("222222222222222222", "disconnect"),
            )
            assert denied.status_code == 200, denied.text
            assert denied.json()["authorized"] is False
            assert denied.json()["executed"] is False
            assert denied.json()["speaker_entity_id"] == "PERSON_LUCAS"
            assert core.platform_presence.snapshot(MessagingPlatform.DISCORD).desired_connected is True

            unknown = client.post(
                "/v1/messaging/worker/presence-control",
                headers=headers,
                json=_payload("999999999999999999", "disconnect"),
            )
            assert unknown.status_code == 200
            assert unknown.json()["authorized"] is False
            assert unknown.json()["speaker_entity_id"] is None

            # Service credential remains transport-only and cannot grant itself capability.
            assert client.put(
                "/v1/operator/messaging/presence-authority/PERSON_LUCAS",
                headers=headers,
                json={"enabled": True, "confirm": True},
            ).status_code == 403
    finally:
        core.close()
