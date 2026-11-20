"""Pinned-DNS outbound browser proxy. No private IP or implicit direct egress.

The browser gets a per-context HTTP proxy on loopback. Every CONNECT / HTTP
GET destination is resolved once; all candidate IPs must be public, then the
outbound TCP socket uses a selected vetted IP. This prevents DNS rebinding
between validation and connection and protects subresources/redirects too.

This is a defense-in-depth control, NOT an alternative to a container egress
firewall: production should additionally deny RFC1918/link-local at the network
boundary (a compromised Chromium process need not honor a proxy).
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
MAX_HEADERS = 16_384
MAX_PIPE_BYTES = 8_000_000
MAX_CONNECTIONS = 24


class ProxyDenied(ValueError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def public_ip_answers(addresses) -> list[str]:
    """Reject mixed public/private answers, mapped v6, multicast and reserved."""
    output = []
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
            if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
                ip = ip.ipv4_mapped
        except ValueError as exc:
            raise ProxyDenied("DNS_ADDRESS_INVALID") from exc
        if not ip.is_global:
            raise ProxyDenied("PRIVATE_NETWORK_DENIED")
        output.append(str(ip))
    if not output:
        raise ProxyDenied("DNS_EMPTY")
    return output


def validate_authority(host: str, port: int) -> tuple[str, int]:
    if not isinstance(host, str) or not host or len(host) > 253:
        raise ProxyDenied("HOST_INVALID")
    if any(c.isspace() or ord(c) < 32 for c in host) or any(
        c in host for c in "@%\\/#?"
    ):
        raise ProxyDenied("HOST_INVALID")
    if port not in ALLOWED_PORTS:
        raise ProxyDenied("PORT_DENIED")
    if host.endswith(".") or host.lower() in ("localhost", "metadata.google.internal"):
        raise ProxyDenied("HOST_DENIED")
    if host.lower().endswith((".localhost", ".local", ".internal", ".onion")):
        raise ProxyDenied("HOST_DENIED")
    try:
        literal = ipaddress.ip_address(host.strip("[]"))
    except ValueError:
        literal = None
    if literal is not None:
        if isinstance(literal, ipaddress.IPv6Address) and literal.ipv4_mapped:
            literal = literal.ipv4_mapped
        if not literal.is_global:
            raise ProxyDenied("PRIVATE_NETWORK_DENIED")
        return str(literal), port
    try:
        hostname = host.encode("idna").decode("ascii").lower()
    except UnicodeError as exc:
        raise ProxyDenied("HOST_INVALID") from exc
    if ":" in hostname:
        raise ProxyDenied("HOST_INVALID")
    return hostname, port


async def resolve_public(host: str, port: int) -> str:
    host, port = validate_authority(host, port)
    try:
        answers = await asyncio.wait_for(
            asyncio.get_running_loop().getaddrinfo(
                host, port, type=socket.SOCK_STREAM
            ), 3.0,
        )
    except (OSError, TimeoutError) as exc:
        raise ProxyDenied("DNS_FAILED") from exc
    return public_ip_answers(answers)[0]


def parse_proxy_target(method: str, target: str) -> tuple[str, int]:
    if method == "CONNECT":
        if not target or "/" in target or "@" in target:
            raise ProxyDenied("PROXY_TARGET_INVALID")
        parsed = urlsplit("https://" + target)
        if parsed.path or parsed.query or parsed.username or not parsed.hostname:
            raise ProxyDenied("PROXY_TARGET_INVALID")
        return validate_authority(parsed.hostname, parsed.port or 443)
    if method not in ("GET", "HEAD"):
        raise ProxyDenied("METHOD_DENIED")
    parsed = urlsplit(target)
    if parsed.scheme != "http" or not parsed.hostname or parsed.username:
        raise ProxyDenied("PROXY_TARGET_INVALID")
    return validate_authority(parsed.hostname, parsed.port or 80)


async def _pipe(source: asyncio.StreamReader, sink: asyncio.StreamWriter) -> None:
    remaining = MAX_PIPE_BYTES
    try:
        while remaining > 0:
            chunk = await asyncio.wait_for(source.read(min(16384, remaining)), 8.0)
            if not chunk:
                break
            remaining -= len(chunk)
            sink.write(chunk)
            await asyncio.wait_for(sink.drain(), 3.0)
    except (OSError, TimeoutError, ConnectionError):
        pass


async def _tunnel(
    browser_reader: asyncio.StreamReader,
    browser_writer: asyncio.StreamWriter,
    remote_reader: asyncio.StreamReader,
    remote_writer: asyncio.StreamWriter,
) -> None:
    left = asyncio.create_task(_pipe(browser_reader, remote_writer))
    right = asyncio.create_task(_pipe(remote_reader, browser_writer))
    try:
        await asyncio.wait({left, right}, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for task in (left, right):
            task.cancel()
        await asyncio.gather(left, right, return_exceptions=True)


def _sanitize_headers(lines: list[bytes], host: str, port: int) -> bytes:
    safe = []
    for raw in lines:
        if not raw:
            continue
        if raw[:1] in (b" ", b"\t") or b":" not in raw:
            raise ProxyDenied("PROXY_HEADER_INVALID")
        name, value = raw.split(b":", 1)
        if not name or any(c < 33 or c > 126 for c in name):
            raise ProxyDenied("PROXY_HEADER_INVALID")
        key = name.decode("ascii", errors="ignore").lower()
        if key in {"host", "connection", "proxy-connection", "proxy-authorization", "proxy-authenticate"}:
            continue
        if b"\x00" in value:
            raise ProxyDenied("PROXY_HEADER_INVALID")
        safe.append(raw)
    netloc = f"[{host}]" if ":" in host else host
    if port != 80:
        netloc += f":{port}"
    return (b"Host: " + netloc.encode("ascii") + b"\r\n"
            + b"\r\n".join(safe) + (b"\r\n" if safe else b"")
            + b"Connection: close\r\n")


class PinnedProxy:
    def __init__(self):
        self._server: asyncio.AbstractServer | None = None
        self._connections = asyncio.Semaphore(MAX_CONNECTIONS)

    async def start(self):
        self._server = await asyncio.start_server(self._handle, "127.0.0.1", 0, limit=MAX_HEADERS)
        return self

    @property
    def url(self) -> str:
        if self._server is None:
            raise RuntimeError("proxy not started")
        return f"http://127.0.0.1:{self._server.sockets[0].getsockname()[1]}"

    async def close(self):
        if self._server:
            self._server.close()
            await self._server.wait_closed()
            self._server = None

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        remote_writer = None
        async with self._connections:
            try:
                head = await asyncio.wait_for(reader.readuntil(b"\r\n\r\n"), 3.0)
                if len(head) > MAX_HEADERS or b"\x00" in head:
                    raise ProxyDenied("PROXY_HEADER_INVALID")
                segments = head[:-4].split(b"\r\n")
                parts = segments[0].decode("ascii").split(" ")
                if len(parts) != 3 or parts[2] != "HTTP/1.1":
                    raise ProxyDenied("PROXY_REQUEST_INVALID")
                method, target, _ = parts
                host, port = parse_proxy_target(method, target)
                ip = await resolve_public(host, port)
                remote_reader, remote_writer = await asyncio.wait_for(
                    asyncio.open_connection(ip, port), 4.0
                )
                if method == "CONNECT":
                    writer.write(b"HTTP/1.1 200 Connection Established\r\n\r\n")
                else:
                    parsed = urlsplit(target)
                    path = parsed.path or "/"
                    if parsed.query:
                        path += "?" + parsed.query
                    if any(ord(c) < 32 for c in path):
                        raise ProxyDenied("PROXY_TARGET_INVALID")
                    headers = _sanitize_headers(segments[1:], host, port)
                    remote_writer.write(
                        (method + " " + path + " HTTP/1.1\r\n").encode("ascii")
                        + headers + b"\r\n"
                    )
                    await remote_writer.drain()
                await writer.drain()
                await _tunnel(reader, writer, remote_reader, remote_writer)
            except (ProxyDenied, OSError, TimeoutError, asyncio.IncompleteReadError,
                    asyncio.LimitOverrunError, ValueError, UnicodeError):
                try:
                    writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Length: 0\r\nConnection: close\r\n\r\n")
                    await writer.drain()
                except (OSError, ConnectionError):
                    pass
            finally:
                if remote_writer is not None:
                    remote_writer.close()
                writer.close()
                try:
                    await writer.wait_closed()
                except (OSError, ConnectionError):
                    pass
