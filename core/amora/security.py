"""Shared pre-routing controls. No cookies, proxy-header trust or wildcard CORS."""

from __future__ import annotations

import asyncio
import base64
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import hmac
import ipaddress
import os
import re
import secrets
from urllib.parse import urlsplit

from starlette.responses import JSONResponse

CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
    "img-src 'self' blob: data:; media-src 'self' blob:; connect-src 'self'; "
    "worker-src 'self' blob:; object-src 'none'; base-uri 'none'; "
    "frame-ancestors 'none'; form-action 'self'"
)


def origin(value: str) -> str:
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"https", "http"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
        or any(c.isspace() for c in value)
    ):
        raise ValueError("invalid public origin")
    port = parsed.port
    host = parsed.hostname.lower()
    if ":" in host:
        host = "[" + host + "]"
    default = 443 if parsed.scheme == "https" else 80
    return (
        parsed.scheme + "://" + host + (f":{port}" if port and port != default else "")
    )


@dataclass(frozen=True)
class SecuritySettings:
    cloud: bool
    origins: frozenset[str]
    hosts: frozenset[str]

    @classmethod
    def from_environment(cls, *, worker=False):
        cloud = os.getenv("AMORA_MODE", "local") == "cloud"
        values = [
            value.strip()
            for value in os.getenv("AMORA_PUBLIC_ORIGINS", "").split(",")
            if value.strip()
        ]
        origins = frozenset(origin(value) for value in values)
        if cloud and not worker and not origins:
            raise ValueError("cloud requires explicit AMORA_PUBLIC_ORIGINS")
        if cloud and any(not value.startswith("https://") for value in origins):
            raise ValueError("cloud public origins must use HTTPS")
        hosts = {urlsplit(value).hostname for value in origins} | {
            "127.0.0.1",
            "localhost",
            "::1",
        }
        return cls(cloud, origins, frozenset(hosts))


def token_equal(first: str, second: str) -> bool:
    # compare_digest(str,str) rejects non-ASCII and could produce a public 500.
    return hmac.compare_digest(first.encode("utf-8"), second.encode("utf-8"))


def issue_browser_token(secret: str, clock, role="web") -> str:
    if role == "web":
        version = "v2"
        payload = f"web:{secrets.token_hex(16)}"
    elif role == "avatar":
        version = "v1"
        payload = (
            f"avatar:{int(clock.now().timestamp()) + 43200}:{secrets.token_hex(16)}"
        )
    else:
        raise ValueError("unsupported browser role")
    encoded = base64.urlsafe_b64encode(payload.encode()).decode().rstrip("=")
    prefix = f"browser.{version}."
    signature = hmac.new(
        secret.encode(), (prefix + encoded).encode(), hashlib.sha256
    ).hexdigest()
    return prefix + encoded + "." + signature


def token_role(token: str, secret: str, clock) -> str | None:
    if token_equal(token, secret):
        return "admin"
    try:
        prefix, version, payload, signature = token.split(".")
        if prefix != "browser" or version not in {"v1", "v2"} or len(payload) > 256:
            return None
        expected = hmac.new(
            secret.encode(), (f"browser.{version}." + payload).encode(), hashlib.sha256
        ).hexdigest()
        if not token_equal(expected, signature):
            return None
        fields = (
            base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4))
            .decode()
            .split(":")
        )
        if version == "v2":
            # Durable, client-scoped Web credential. Rotation of the signing
            # admin secret revokes it; it never gains administrative access.
            role, nonce = fields
            if role != "web":
                return None
        else:
            role, expiry, nonce = fields
            if role not in {"web", "avatar"} or int(expiry) <= clock.now().timestamp():
                return None
        if not re.fullmatch("[a-f0-9]{32}", nonce):
            return None
        return role
    except (ValueError, UnicodeError):
        return None


class RateLimits:
    def __init__(self, clock):
        self.clock = clock
        self.entries = OrderedDict()

    def allow(self, category, peer, maximum, seconds=60):
        now = self.clock.monotonic()
        # Expire inactive entries, then reject new keys at capacity: no eviction bypass.
        for key, (start, count) in list(self.entries.items()):
            if now - start >= seconds:
                del self.entries[key]
        key = (category, peer)
        if key not in self.entries:
            if len(self.entries) >= 4096:
                return False
            self.entries[key] = (now, 0)
        start, count = self.entries[key]
        if count >= maximum:
            return False
        self.entries[key] = (start, count + 1)
        return True


class SecurityMiddleware:
    def __init__(
        self, app, *, settings, clock, authenticate, web_access=None, worker=False
    ):
        self.app, self.settings, self.clock = app, settings, clock
        self.authenticate, self.web_access, self.worker = (
            authenticate,
            web_access,
            worker,
        )
        self.limits = RateLimits(clock)
        self.expensive_running = 0
        self.bodies_running = 0
        self.sockets = {}

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            return await self.app(scope, receive, send)
        websocket = scope["type"] == "websocket"
        path = scope["path"]
        raw = scope.get("headers", [])
        headers = {k.lower(): v for k, v in raw}
        peer = scope.get("client") or ("unknown", 0)
        peer = peer[0]

        async def respond(code, detail):
            if websocket:
                await send(
                    {"type": "websocket.close", "code": 4401 if code == 401 else 4403}
                )
            else:
                await JSONResponse(
                    {"detail": detail},
                    status_code=code,
                    headers={"Retry-After": "60"} if code == 429 else None,
                )(scope, receive, secured_send)

        async def secured_send(message):
            if message["type"] == "http.response.start":
                existing = {k.lower() for k, v in message.get("headers", [])}
                security = {
                    b"content-security-policy": CSP.encode(),
                    b"x-content-type-options": b"nosniff",
                    b"referrer-policy": b"no-referrer",
                    b"permissions-policy": b"camera=(), microphone=(self), geolocation=(), payment=(), usb=()",
                    b"x-frame-options": b"DENY",
                    b"cache-control": b"no-store",
                }
                if self.settings.cloud:
                    security[b"strict-transport-security"] = b"max-age=31536000"
                message["headers"] = list(message.get("headers", [])) + [
                    (k, v) for k, v in security.items() if k not in existing
                ]
            await send(message)

        for name in [b"host", b"origin", b"authorization", b"content-length"]:
            if sum(k.lower() == name for k, v in raw) > 1:
                return await respond(400, "duplicate header")
        if sum(len(k) + len(v) for k, v in raw) > 16384 or any(
            any(c < 32 or c == 127 for c in v) for k, v in raw
        ):
            return await respond(400, "invalid headers")
        try:
            host = headers.get(b"host", b"").decode("ascii")
            parsed = urlsplit("http://" + host)
            if (
                not parsed.hostname
                or parsed.username is not None
                or parsed.password is not None
                or parsed.path
                or parsed.query
                or parsed.fragment
            ):
                raise ValueError()
            _ = parsed.port
            if (
                self.settings.cloud
                and not self.worker
                and parsed.hostname.lower() not in self.settings.hosts
            ):
                return await respond(400, "untrusted host")
            supplied_origin = headers.get(b"origin")
            if supplied_origin:
                supplied = supplied_origin.decode("ascii")
                allowed = self.settings.origins or {
                    origin(
                        ("https" if scope.get("scheme") in {"https", "wss"} else "http")
                        + "://"
                        + host
                    )
                }
                if supplied not in allowed:
                    return await respond(403, "origin denied")
        except (ValueError, UnicodeError):
            return await respond(400, "invalid host or origin")
        if not self.limits.allow("requests", peer, 600):
            return await respond(429, "request limit reached")
        public = (
            path in {"/health", "/ready", "/live", "/v1/pairing/redeem"}
            and not self.worker
        )
        public = public or (path == "/live" and self.worker)
        public = public or (
            not websocket
            and scope["method"] in {"GET", "HEAD"}
            and path.startswith(("/web", "/avatar"))
            and not self.worker
        )
        if (
            len(scope.get("query_string", b"")) > 8192
            or len(path.encode("utf-8", "replace")) > 4096
        ):
            return await respond(414, "request target too long")
        if any(ord(c) < 32 or ord(c) == 127 for c in path):
            return await respond(400, "invalid request target")
        role = None
        if not public:
            authorization = headers.get(b"authorization", b"").decode(
                "utf-8", "replace"
            )
            if len(authorization) <= 4096 and authorization.startswith("Bearer "):
                role = self.authenticate(authorization[7:])
            if role is None:
                if not self.limits.allow("auth_fail", peer, 20):
                    return await respond(429, "authentication limit reached")
                return await respond(401, "authentication required")
            if role not in {"admin", "service"} and (
                websocket or not self.web_access or not self.web_access(role, scope)
            ):
                return await respond(403, "credential scope denied")
        scope.setdefault("state", {})["security_role"] = role
        if path == "/v1/pairing/redeem":
            if not self.limits.allow("pair", peer, 5) or not self.limits.allow(
                "pair_global", "all", 10
            ):
                return await respond(429, "pairing limit reached")
        if path in {"/health", "/ready"} and (
            not self.limits.allow("probes", peer, 60)
            or not self.limits.allow("probes_global", "all", 120)
        ):
            return await respond(429, "probe limit reached")
        if websocket:
            if self.sockets.get(peer, 0) >= 2 or sum(self.sockets.values()) >= 32:
                return await respond(429, "connection limit reached")
            self.sockets[peer] = self.sockets.get(peer, 0) + 1
            try:
                async with asyncio.timeout(1800):
                    await self.app(scope, receive, secured_send)
            except TimeoutError:
                await send({"type": "websocket.close", "code": 1001})
            finally:
                self.sockets[peer] -= 1
                if not self.sockets[peer]:
                    del self.sockets[peer]
            return
        expensive = (
            path.startswith(("/v1/web/voice/", "/v1/voice/utterances/"))
            or path
            in {
                "/v1/render",
                "/v1/audio/transcribe",
                "/v1/web/utterance",
                "/v1/cognition/respond",
                "/v1/actions/execute",
                "/v1/autonomy/cycle",
            }
            or path.startswith("/v1/cognition/replay/")
            or path in {"/v1/state", "/v1/operations/state"}
            or (
                scope["method"] in {"POST", "PUT", "DELETE", "PATCH"}
                and path.startswith("/v1/")
                and path != "/v1/pairing/redeem"
                and not path.endswith(("/started", "/ack", "/cancel"))
            )
        )
        if expensive:
            if (
                not self.limits.allow("expensive", peer, 20)
                or not self.limits.allow("expensive_global", "all", 60)
                or self.expensive_running >= 4
            ):
                return await respond(429, "work limit reached")
        if self.bodies_running >= 32:
            return await respond(429, "body concurrency limit reached")
        self.bodies_running += 1
        if expensive:
            self.expensive_running += 1
        try:
            if headers.get(b"content-encoding", b"identity") != b"identity":
                return await respond(415, "content encoding is not supported")
            maximum = (
                12 * 1024 * 1024
                if path == "/v1/audio/transcribe"
                or path.startswith("/v1/avatar/assets/")
                else 256 * 1024
            )
            if path == "/v1/pairing/redeem" or self.worker:
                maximum = 16384
            content_length = headers.get(b"content-length")
            if content_length is not None:
                if not re.fullmatch(b"[0-9]{1,12}", content_length):
                    return await respond(400, "invalid content length")
                if int(content_length) > maximum:
                    return await respond(413, "body too large")
            if content_length is not None and b"transfer-encoding" in headers:
                return await respond(400, "ambiguous request framing")
            chunks = []
            size = 0
            try:
                async with asyncio.timeout(10):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        chunk = message.get("body", b"")
                        size += len(chunk)
                        if size > maximum:
                            return await respond(413, "body too large")
                        chunks.append(chunk)
                        if not message.get("more_body", False):
                            break
            except TimeoutError:
                return await respond(408, "body timeout")
            consumed = False

            async def bounded_receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {
                        "type": "http.request",
                        "body": b"".join(chunks),
                        "more_body": False,
                    }
                return await receive()

            started = False

            async def tracked_send(message):
                nonlocal started
                if message["type"] == "http.response.start":
                    started = True
                await secured_send(message)

            try:
                async with asyncio.timeout(90):
                    await self.app(scope, bounded_receive, tracked_send)
            except TimeoutError:
                if not started:
                    await respond(504, "request timeout")
        finally:
            self.bodies_running -= 1
            if expensive:
                self.expensive_running -= 1


def trusted_proxy_ips():
    values = [
        value.strip()
        for value in os.getenv("AMORA_TRUSTED_PROXY_IPS", "").split(",")
        if value.strip()
    ]
    for value in values:
        if value == "*":
            raise ValueError("wildcard trusted proxies are forbidden")
        network = ipaddress.ip_network(value, strict=False)
        if (
            network.prefixlen == 0
            or network.network_address.is_unspecified
            or network.network_address.is_multicast
        ):
            raise ValueError("unbounded or invalid trusted proxy network")
    return ",".join(values)


def validate_remote_origin(value: str) -> str:
    """Operator-configured public HTTPS origins only; redirects stay disabled.

    DNS is resolved by the configured proxy, so private-network egress denial
    must also be enforced there (including rebinding). No user route accepts URLs.
    """
    if (
        not isinstance(value, str)
        or not value.isascii()
        or any(ord(c) <= 32 or ord(c) == 127 for c in value)
        or "\\" in value
        or "?" in value
        or "#" in value
        or "%" in value
    ):
        raise ValueError("invalid remote origin")
    parsed = urlsplit(value)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
        or parsed.port not in {None, 443}
    ):
        raise ValueError("remote origin requires public HTTPS on port 443")
    host = parsed.hostname or ""
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if (
            len(host) > 253
            or "." not in host
            or host.endswith((".", ".localhost", ".local", ".internal"))
            or host.rsplit(".", 1)[-1].isdigit()
            or re.fullmatch(r"[0-9.]+", host)
            or not all(
                re.fullmatch(r"[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?", label)
                for label in host.split(".")
            )
        ):
            raise ValueError("invalid public hostname")
    else:
        if not address.is_global:
            raise ValueError("private or reserved remote address")
    return value.rstrip("/")
