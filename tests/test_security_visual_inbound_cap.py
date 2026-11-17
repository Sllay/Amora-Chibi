"""Regression: authenticated image transport must not be blocked by the 256 KiB gate.

Privacy boundaries are preserved: only messaging service POST inbound gains a
6 MiB JSON envelope; every other route and principal retains existing caps.
"""
from __future__ import annotations

from base64 import b64encode
import json

import pytest
from fastapi.testclient import TestClient

from amora.app import AmoraCore
from amora.api import create_api
from amora.time import SimulatedClock


@pytest.fixture
def cloud_messaging(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    monkeypatch.setenv("AMORA_MODE", "cloud")
    monkeypatch.setenv("AMORA_PUBLIC_ORIGINS", "https://amora.example")
    token = "messaging-worker-private-test-token-" + "x" * 40
    monkeypatch.setenv("AMORA_MESSAGING_SERVICE_TOKEN", token)
    app = create_api(core)
    with TestClient(app, base_url="https://amora.example") as client:
        yield core, client, {"Authorization": "Bearer " + token}
    core.close()


def test_messaging_image_json_over_256_kib_reaches_schema_validation(cloud_messaging):
    _, client, service = cloud_messaging
    path = "/v1/messaging/worker/inbound"
    # Represents an image-bearing worker request; avoid recording actual art.
    # 4 MiB source bytes become 5,592,408 Base64 characters plus JSON.
    image_base64 = b64encode(b"\x00" * (4 * 1024 * 1024))
    payload = (
        b'{"incoming_image":{"attachment_id":"123456789012345678",'
        b'"content_mime":"image/png","content_base64":"'
        + image_base64 + b'"}}'
    )
    assert 256 * 1024 < len(payload) < 6 * 1024 * 1024

    # The incomplete model must be rejected by Pydantic (422), not by the
    # pre-routing size guard (413). No perception API call is made.
    response = client.post(
        path, content=payload,
        headers={**service, "Content-Type": "application/json"},
    )
    assert response.status_code == 422
    assert response.json() == {"detail": "invalid request"}


@pytest.mark.parametrize("kind", ["wrong_role", "wrong_path", "wrong_method", "no_auth"])
def test_large_body_not_granted_to_other_routes_or_roles(cloud_messaging, kind):
    core, client, service = cloud_messaging
    headers = dict(service)
    path, method = "/v1/messaging/worker/inbound", "POST"
    if kind == "wrong_role":
        headers["Authorization"] = "Bearer " + core.secrets.get("LOCAL_API_TOKEN")
    elif kind == "wrong_path":
        path = "/v1/messaging/worker/inbound/other"
    elif kind == "wrong_method":
        method = "PUT"
    elif kind == "no_auth":
        headers.pop("Authorization")
    response = client.request(
        method, path,
        content=b"\x00" * (256 * 1024 + 1),
        headers={**headers, "Content-Type": "application/json"},
    )
    assert response.status_code == {"no_auth": 401, "wrong_method": 403}.get(kind, 413)


def test_messaging_inbound_has_finite_6_mib_cap(cloud_messaging):
    _, client, service = cloud_messaging
    response = client.post(
        "/v1/messaging/worker/inbound",
        content=b"{}",
        headers={**service, "Content-Length": str(6 * 1024 * 1024 + 1)},
    )
    assert response.status_code == 413
    assert response.json() == {"detail": "body too large"}


@pytest.mark.asyncio
async def test_chunked_image_payload_still_stops_on_six_mib(cloud_messaging):
    import httpx

    _, client, service = cloud_messaging
    emitted = 0

    async def chunks():
        nonlocal emitted
        for _ in range(8):
            emitted += 1
            yield b"z" * (1024 * 1024)

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app),
        base_url="https://amora.example",
    ) as direct:
        result = await direct.post(
            "/v1/messaging/worker/inbound", content=chunks(), headers=service,
        )
    assert result.status_code == 413
    assert emitted == 7  # the seventh MiB crosses the six MiB cap


def test_other_endpoints_retain_original_256_kib_limit(cloud_messaging):
    core, client, _ = cloud_messaging
    admin = {"Authorization": "Bearer " + core.secrets.get("LOCAL_API_TOKEN")}
    response = client.post(
        "/v1/web/utterance",
        content=b"\x00" * (256 * 1024 + 1),
        headers=admin,
    )
    assert response.status_code == 413
