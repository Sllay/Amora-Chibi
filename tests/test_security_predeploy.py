"""Adversarial pre-routing, credential scope and public-surface regressions."""

from datetime import timedelta
import json
import logging
import random
import re
from uuid import uuid4

import httpx
import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from amora.app import AmoraCore
from amora.api import create_api
from amora.security import validate_remote_origin, trusted_proxy_ips, token_role
from amora.time import SimulatedClock


@pytest.fixture
def cloud(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path))
    core = AmoraCore.bootstrap(clock=SimulatedClock())
    monkeypatch.setenv("AMORA_MODE", "cloud")
    monkeypatch.setenv("AMORA_PUBLIC_ORIGINS", "https://amora.example")
    app = create_api(core)
    headers = {"Authorization": "Bearer " + core.secrets.get("LOCAL_API_TOKEN")}
    with TestClient(app, base_url="https://amora.example") as client:
        yield core, client, headers
    core.close()


def test_every_documented_private_endpoint_requires_auth_before_parsing(cloud):
    core, client, headers = cloud
    paths = client.app.openapi()["paths"]
    checked = 0
    for path, operations in paths.items():
        if not path.startswith("/v1/") or path == "/v1/pairing/redeem":
            continue
        url = re.sub(r"\{[^}]+\}", str(uuid4()), path)
        for method in operations:
            if method not in {"get", "put", "post", "delete", "patch"}:
                continue
            core.clock.advance(timedelta(seconds=61))
            response = client.request(method, url, content=b"invalid-and-private")
            assert response.status_code == 401, (method, path, response.status_code)
            checked += 1
    assert checked >= 50
    assert client.get("/v1/operations/state", headers=headers).status_code == 200
    assert client.get("/openapi.json").status_code == 401
    assert client.get("/docs").status_code == 401


def test_cloud_pairing_returns_durable_web_scoped_credential(cloud):
    core, client, headers = cloud
    response = client.post(
        "/v1/pairing/redeem", json={"code": core.pairing.issue().code}
    )
    assert response.status_code == 200
    token = response.json()["token"]
    assert token != core.secrets.get("LOCAL_API_TOKEN")
    browser = {"Authorization": "Bearer " + token}
    assert client.get("/v1/web/state", headers=browser).status_code == 200
    for path in [
        "/v1/operations/state",
        "/v1/state",
        "/v1/cognition/journal",
        "/v1/avatar/project",
    ]:
        assert client.get(path, headers=browser).status_code == 403
    assert (
        client.get(
            "/v1/conversation/next?device_id=DEVICE_A15", headers=browser
        ).status_code
        == 403
    )
    assert (
        client.get(
            "/v1/conversation/next?device_id=WEB_SAFARI", headers=browser
        ).status_code
        == 200
    )
    core.clock.advance(timedelta(days=365))
    assert client.get("/v1/web/state", headers=browser).status_code == 200
    assert client.get("/v1/operations/state", headers=browser).status_code == 403
    assert token_role(token, "rotated-admin-secret", core.clock) is None
    assert (
        token_role(token + "x", core.secrets.get("LOCAL_API_TOKEN"), core.clock) is None
    )


def test_avatar_pairing_cannot_access_console(cloud):
    core, client, _ = cloud
    token = client.post(
        "/v1/pairing/redeem",
        json={"code": core.pairing.issue().code, "client": "avatar"},
    ).json()["token"]
    headers = {"Authorization": "Bearer " + token}
    assert client.get("/v1/avatar/project", headers=headers).status_code == 200
    assert client.get("/v1/avatar/admin/future", headers=headers).status_code == 403
    assert client.delete("/v1/avatar/project", headers=headers).status_code == 403
    assert client.get("/v1/operations/state", headers=headers).status_code == 403


def test_security_headers_cors_and_minimal_probes(cloud):
    _, client, headers = cloud
    for path in ["/web/", "/avatar/", "/health", "/ready", "/v1/state"]:
        response = client.get(path)
        assert response.headers["x-content-type-options"] == "nosniff"
        assert response.headers["referrer-policy"] == "no-referrer"
        assert "microphone=(self)" in response.headers["permissions-policy"]
        assert "script-src 'self';" in response.headers["content-security-policy"]
        assert "unsafe-eval" not in response.headers["content-security-policy"]
        assert "access-control-allow-origin" not in response.headers
        assert "set-cookie" not in response.headers
    assert set(client.get("/health").json()) == {"status"}
    assert set(client.get("/ready").json()) == {"status"}
    assert (
        client.get(
            "/v1/operations/state",
            headers={**headers, "Origin": "https://evil.example"},
        ).status_code
        == 403
    )
    assert (
        client.options(
            "/v1/web/utterance",
            headers={
                "Origin": "https://evil.example",
                "Access-Control-Request-Method": "POST",
            },
        ).status_code
        == 403
    )
    assert client.get("/live", headers={"Host": "evil.example"}).status_code == 400
    assert client.get("/live", headers={"Origin": "null"}).status_code == 403


def test_pairing_rate_limit_ignores_spoofed_forwarded_headers(cloud):
    _, client, _ = cloud
    for attempt in range(5):
        assert (
            client.post(
                "/v1/pairing/redeem",
                json={"code": "000000"},
                headers={"X-Forwarded-For": f"8.8.8.{attempt}"},
            ).status_code
            == 401
        )
    assert client.post("/v1/pairing/redeem", json={"code": "000000"}).status_code == 429


def test_public_pairing_fuzz_does_not_echo_input_or_raise_500(cloud):
    core, client, _ = cloud
    rng = random.Random(17)
    cases = [
        None,
        False,
        [],
        {},
        -1,
        0,
        "",
        "٠١٢٣٤٥",
        "１２３４５６",
        "x" * 17000,
        "\x00" * 6,
        "<script>secret</script>",
    ]
    cases += [
        "".join(rng.choice("ABC0123456789\x00é") for _ in range(rng.randrange(0, 80)))
        for _ in range(80)
    ]
    for value in cases:
        core.clock.advance(timedelta(seconds=61))
        response = client.post("/v1/pairing/redeem", json={"code": value})
        assert response.status_code in {401, 413, 422}, (
            type(value),
            response.status_code,
        )
        assert response.json().get("detail") in {
            "invalid or expired pairing code",
            "invalid request",
            "body too large",
        }


def test_body_limits_and_validation_redaction(cloud):
    _, client, headers = cloud
    assert (
        client.post(
            "/v1/web/utterance", content=b"x" * (256 * 1024 + 1), headers=headers
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/v1/audio/transcribe",
            content=b"ignored",
            headers={**headers, "Content-Length": str(12 * 1024 * 1024 + 1)},
        ).status_code
        == 413
    )
    assert (
        client.post(
            "/v1/web/utterance",
            content=b"ignored",
            headers={**headers, "Content-Encoding": "gzip"},
        ).status_code
        == 415
    )
    assert client.post(
        "/v1/web/utterance",
        json={"text": {"secret": "never-echo-this"}},
        headers=headers,
    ).json() == {"detail": "invalid request"}
    assert (
        client.get(
            "/v1/state", headers=[(b"authorization", b"Bearer \xff")]
        ).status_code
        == 401
    )
    assert (
        client.get(
            "/live", headers=[("Host", "amora.example"), ("Host", "evil.example")]
        ).status_code
        == 400
    )


def test_websocket_origin_auth_and_query_token(cloud):
    _, client, headers = cloud
    for kwargs, code in [
        ({"headers": {"Origin": "https://evil.example", **headers}}, 4403),
        ({"headers": {"Origin": "https://amora.example"}}, 4401),
        ({"headers": {}}, 4401),
    ]:
        with pytest.raises(WebSocketDisconnect) as caught:
            with client.websocket_connect(
                "wss://amora.example/v1/events/stream?token=never-accept-query-auth",
                **kwargs,
            ):
                pass
        assert caught.value.code == code
    with client.websocket_connect(
        "wss://amora.example/v1/events/stream",
        headers={"Origin": "https://amora.example", **headers},
    ):
        pass


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com",
        "https://localhost",
        "https://127.0.0.1",
        "https://[::1]",
        "https://169.254.169.254",
        "https://10.0.0.1",
        "https://[::ffff:127.0.0.1]",
        "https://2130706433",
        "https://0x7f000001",
        "https://0177.0.0.1",
        "https://0x7f.0.0.1",
        "https://metadata.google.internal",
        "https://example.com@127.0.0.1",
        "https://user:password@example.com",
        "https://example.com:8080",
        "https://example.com?key=secret",
        "https://example.com#fragment",
        "https://example.com%2f@127.0.0.1",
        "https://example.com\\@127.0.0.1",
        "https://example.com\n",
    ],
)
def test_ssrf_remote_origin_validation(url):
    with pytest.raises(ValueError):
        validate_remote_origin(url)


def test_trusted_proxy_wildcard_forbidden(monkeypatch):
    monkeypatch.setenv("AMORA_TRUSTED_PROXY_IPS", "*")
    with pytest.raises(ValueError):
        trusted_proxy_ips()
    monkeypatch.setenv("AMORA_TRUSTED_PROXY_IPS", "10.0.0.0/24,127.0.0.1")
    assert trusted_proxy_ips() == "10.0.0.0/24,127.0.0.1"


def test_logs_redact_secret_values_headers_and_query_keys():
    from amora.observability import JsonFormatter, remember_secret

    secret = 'private-quote-"-secret-value'
    remember_secret(secret)
    record = logging.LogRecord(
        "test",
        logging.ERROR,
        "",
        0,
        "secret=%s Bearer unknown-token https://api.example?key=other-secret",
        (secret,),
        None,
    )
    record.payload = {"token": secret}
    output = JsonFormatter().format(record)
    assert secret not in output and "private-quote-" not in output
    assert "unknown-token" not in output and "other-secret" not in output
    json.loads(output)


async def test_chunked_upload_limit_is_enforced_while_reading(cloud):
    core, client, headers = cloud
    count = 0

    async def chunks():
        nonlocal count
        for _ in range(8):
            count += 1
            yield b"x" * 100000

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=client.app), base_url="https://amora.example"
    ) as direct:
        response = await direct.post(
            "/v1/web/utterance", content=chunks(), headers=headers
        )
        assert response.status_code == 413
        assert count == 3


async def test_slow_body_timeout_without_waiting_real_seconds():
    from amora.security import SecurityMiddleware, SecuritySettings
    from amora.time import SimulatedClock

    messages = []

    async def app(scope, receive, send):
        pytest.fail("slow body reached handler")

    async def receive():
        raise TimeoutError()

    async def send(message):
        messages.append(message)

    gate = SecurityMiddleware(
        app,
        settings=SecuritySettings(False, frozenset(), frozenset()),
        clock=SimulatedClock(),
        authenticate=lambda token: None,
    )
    scope = {
        "type": "http",
        "method": "POST",
        "path": "/v1/pairing/redeem",
        "headers": [(b"host", b"testserver")],
        "client": ("peer", 1),
        "scheme": "http",
    }
    await gate(scope, receive, send)
    assert messages[0]["status"] == 408


@pytest.mark.parametrize("network", ["*", "0.0.0.0/0", "::/0", "0.0.0.0", "::"])
def test_all_network_proxy_trust_is_rejected(network, monkeypatch):
    monkeypatch.setenv("AMORA_TRUSTED_PROXY_IPS", network)
    with pytest.raises(ValueError):
        trusted_proxy_ips()


def test_weak_cloud_administrator_token_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path))
    core = AmoraCore.bootstrap()
    try:
        core.secrets.set("LOCAL_API_TOKEN", "weak")
        monkeypatch.setenv("AMORA_MODE", "cloud")
        monkeypatch.setenv("AMORA_PUBLIC_ORIGINS", "https://amora.example")
        with pytest.raises(ValueError, match="32 bytes"):
            create_api(core)
    finally:
        core.close()


def test_malformed_public_json_is_bounded_and_not_echoed(cloud):
    core, client, _ = cloud
    for body in [b"{", b"\xff", b"[" * 500 + b"]" * 500, b'{"code":NaN}', b"null"]:
        core.clock.advance(timedelta(seconds=61))
        response = client.post(
            "/v1/pairing/redeem",
            content=body,
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code in {400, 422}
        assert "NaN" not in response.text


def test_public_targets_and_parameter_explosion_are_bounded(cloud):
    _, client, _ = cloud
    assert client.get("/live?" + "a=1&" * 3000).status_code == 414
    assert client.get("/web/" + "x" * 5000).status_code == 414
    assert client.get("/web/%00").status_code == 400
    assert client.get("/web/../.env").status_code in {401, 404}


def test_expensive_voice_endpoint_is_rate_limited(cloud, monkeypatch):
    from test_web_safari_v101 import _enqueue_web
    from amora.voice.local_amora import LocalVoiceUnavailable

    core, client, headers = cloud
    item = _enqueue_web(core, "Texto preservado.", "security-rate")

    def unavailable(item):
        raise LocalVoiceUnavailable("VOICE_OFFLINE")

    monkeypatch.setattr(client.app.state.web_local_voice, "render", unavailable)
    for _ in range(20):
        assert (
            client.get(
                f"/v1/web/voice/utterances/{item.id}/audio", headers=headers
            ).status_code
            == 503
        )
    assert (
        client.get(
            f"/v1/web/voice/utterances/{item.id}/audio", headers=headers
        ).status_code
        == 429
    )


def test_worker_does_not_queue_native_renders_behind_a_busy_model():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from amora.voice.worker import create_worker
    from test_remote_voice import Renderer

    renderer = Renderer()
    entered, release = threading.Event(), threading.Event()
    original = renderer.render

    def blocking(item):
        entered.set()
        assert release.wait(3)
        return original(item)

    renderer.render = blocking
    with TestClient(create_worker(renderer, "test-service-only")) as client:
        headers = {"Authorization": "Bearer test-service-only"}
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(
                client.post, "/v1/render", json={"text": "Primeiro"}, headers=headers
            )
            assert entered.wait(3)
            try:
                assert (
                    client.post(
                        "/v1/render", json={"text": "Segundo"}, headers=headers
                    ).status_code
                    == 503
                )
            finally:
                release.set()
            assert pending.result().status_code == 200


async def test_concurrency_slots_cover_slow_uploads_and_release_on_rejection():
    import asyncio
    from amora.security import SecurityMiddleware, SecuritySettings
    from amora.time import SimulatedClock

    entered, release = asyncio.Event(), asyncio.Event()
    count = 0
    responses = []

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        nonlocal count
        count += 1
        if count == 4:
            entered.set()
        await release.wait()
        return {"type": "http.request", "body": b"{}", "more_body": False}

    async def forbidden_receive():
        pytest.fail("concurrency-rejected request read its body")

    async def send(message):
        if message["type"] == "http.response.start":
            responses.append(message["status"])

    gate = SecurityMiddleware(
        app,
        settings=SecuritySettings(False, frozenset(), frozenset()),
        clock=SimulatedClock(),
        authenticate=lambda token: "admin",
    )

    def scope():
        return {
            "type": "http",
            "method": "POST",
            "path": "/v1/web/utterance",
            "headers": [(b"host", b"testserver"), (b"authorization", b"Bearer test")],
            "client": ("peer", 1),
            "scheme": "http",
        }

    pending = [asyncio.create_task(gate(scope(), receive, send)) for _ in range(4)]
    await asyncio.wait_for(entered.wait(), 2)
    try:
        await gate(scope(), forbidden_receive, send)
        assert responses == [429]
    finally:
        release.set()
        await asyncio.gather(*pending)
    assert gate.expensive_running == gate.bodies_running == 0
    oversized = scope()
    oversized["headers"].append((b"content-length", b"999999"))
    await gate(oversized, forbidden_receive, send)
    assert responses[-1] == 413
    assert gate.expensive_running == gate.bodies_running == 0


def test_avatar_credentials_still_expire_and_web_cannot_be_retagged(cloud):
    import base64
    import hashlib
    import hmac
    from amora.security import issue_browser_token

    core, client, _ = cloud
    secret = core.secrets.get("LOCAL_API_TOKEN")
    avatar = issue_browser_token(secret, core.clock, "avatar")
    core.clock.advance(timedelta(hours=12, seconds=1))
    assert token_role(avatar, secret, core.clock) is None
    # Even a correctly signed v2 payload cannot grant admin/Avatar privileges.
    for role in ["admin", "avatar"]:
        payload = (
            base64.urlsafe_b64encode((role + ":" + "a" * 32).encode())
            .decode()
            .rstrip("=")
        )
        signature = hmac.new(
            secret.encode(), ("browser.v2." + payload).encode(), hashlib.sha256
        ).hexdigest()
        assert (
            token_role("browser.v2." + payload + "." + signature, secret, core.clock)
            is None
        )


def test_durable_web_credential_survives_core_restart_without_gaining_admin(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("AMORA_HOME", str(tmp_path))
    first = AmoraCore.bootstrap()
    try:
        with TestClient(create_api(first)) as client:
            token = client.post(
                "/v1/pairing/redeem", json={"code": first.pairing.issue().code}
            ).json()["token"]
    finally:
        first.close()
    again = AmoraCore.bootstrap()
    try:
        with TestClient(create_api(again)) as client:
            headers = {"Authorization": "Bearer " + token}
            assert client.get("/v1/web/state", headers=headers).status_code == 200
            assert (
                client.get("/v1/operations/state", headers=headers).status_code == 403
            )
            assert (
                client.get(
                    "/v1/conversation/next?device_id=DEVICE_A15", headers=headers
                ).status_code
                == 403
            )
    finally:
        again.close()
