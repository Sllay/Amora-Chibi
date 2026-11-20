"""Browser worker Run 2 contracts: no live web or Chromium required in Core CI."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import httpx
import pytest

from amora.web_tools.browser_client import BrowserClient
from amora.web_tools.navigator import WebFetchError, WebNavigator
from workers.browser.proxy import (
    PinnedProxy, ProxyDenied, parse_proxy_target,
    public_ip_answers, validate_authority,
)
from workers.browser.server import (
    BrowserRenderer, _block_socket, _guard_http, app, checked_web_url,
)


@pytest.mark.parametrize("address", [
    "127.0.0.1", "::1", "10.2.4.7", "192.168.1.4",
    "169.254.169.254", "172.16.3.1", "::ffff:127.0.0.1",
    "fc00::1", "fe80::1", "224.0.0.1", "0.0.0.0",
])
def test_worker_dns_proxy_rejects_private_and_reserved_targets(address):
    with pytest.raises(ProxyDenied):
        public_ip_answers([(None, None, None, None, (address, 443))])


def test_worker_dns_proxy_rejects_mixed_answers_but_allows_public():
    assert public_ip_answers([
        (None, None, None, None, ("8.8.8.8", 443)),
        (None, None, None, None, ("1.1.1.1", 443)),
    ]) == ["8.8.8.8", "1.1.1.1"]
    with pytest.raises(ProxyDenied):
        public_ip_answers([
            (None, None, None, None, ("8.8.8.8", 443)),
            (None, None, None, None, ("127.0.0.1", 443)),
        ])


@pytest.mark.parametrize("host,port", [
    ("localhost", 443), ("foo.local", 443), ("foo.internal", 443),
    ("127.0.0.1", 443), ("169.254.169.254", 80),
    ("example.org", 5432), ("bad@host", 443),
])
def test_worker_proxy_disallows_private_names_and_nonweb_ports(host, port):
    with pytest.raises(ProxyDenied):
        validate_authority(host, port)


@pytest.mark.parametrize("method,target", [
    ("CONNECT", "127.0.0.1:443"),
    ("CONNECT", "example.org:3306"),
    ("GET", "http://localhost/"),
    ("POST", "http://example.org/data"),
    ("GET", "file:///etc/shadow"),
    ("CONNECT", "example.org@127.0.0.1:443"),
])
def test_proxy_rejects_ssrf_targets_and_mutating_methods(method, target):
    with pytest.raises(ProxyDenied):
        parse_proxy_target(method, target)


@pytest.mark.asyncio
async def test_proxy_rejects_loopback_connect_on_real_listening_socket():
    proxy = await PinnedProxy().start()
    try:
        address = proxy.url.replace("http://", "").split(":")
        reader, writer = await asyncio.open_connection(address[0], int(address[1]))
        writer.write(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
        await writer.drain()
        response = await asyncio.wait_for(reader.read(256), timeout=3)
        assert response.startswith(b"HTTP/1.1 502 Bad Gateway")
        writer.close()
        await writer.wait_closed()
    finally:
        await proxy.close()


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/admin", "http://169.254.169.254/",
    "file:///etc/passwd", "data:text/html,hello", "javascript:alert(1)",
    "https://user:pass@example.com/", "http://localhost:8080/admin",
    "https://example.com:4444/",
])
def test_worker_browser_rejects_nonpublic_navigation(url):
    with pytest.raises(ProxyDenied):
        checked_web_url(url)


class FakeBrowser:
    def __init__(self):
        self.options = None
        self.context = FakeContext()

    async def new_context(self, **kwargs):
        self.options = kwargs
        return self.context


class FakeContext:
    def __init__(self):
        self.page = FakePage()
        self.routes = []
        self.closed = False

    async def route(self, *args):
        self.routes.append(("http", args[0]))

    async def route_web_socket(self, *args):
        self.routes.append(("socket", args[0]))

    async def new_page(self):
        return self.page

    async def close(self):
        self.closed = True


class FakePage:
    url = "https://example.org/js"
    def set_default_timeout(self, value):
        pass
    def set_default_navigation_timeout(self, value):
        pass
    async def goto(self, url, **kwargs):
        self.url = url
        return SimpleNamespace(status=200)
    async def wait_for_timeout(self, value):
        pass
    async def evaluate(self, code):
        return {"title": "JavaScript rendered",
                "text": "Texto produzido no DOM depois do JavaScript.",
                "links": [
                    {"url": "https://example.org/other", "title": "Next"},
                    {"url": "http://127.0.0.1/admin", "title": "Private"},
                    {"url": "javascript:alert(1)", "title": "Script"},
                ]}


@pytest.mark.asyncio
async def test_real_renderer_contract_uses_ephemeral_context_and_proxy():
    browser = FakeBrowser()
    result = await BrowserRenderer(browser, "http://127.0.0.1:5555").render(
        "https://example.org/js"
    )
    assert result["status"] == "opened"
    assert result["render_mode"] == "chromium_js_v1"
    assert result["links"] == [{"url": "https://example.org/other", "title": "Next"}]
    assert browser.options["proxy"]["server"] == "http://127.0.0.1:5555"
    assert browser.options["service_workers"] == "block"
    assert browser.options["accept_downloads"] is False
    assert ("socket", "**/*") in browser.context.routes
    assert ("http", "**/*") in browser.context.routes
    assert browser.context.closed


@pytest.mark.asyncio
async def test_browser_blocks_untrusted_subresource_post_and_websockets():
    for method, url, should_block in [
        ("POST", "https://example.org/form", True),
        ("GET", "http://127.0.0.1/secret", True),
        ("GET", "https://example.org/main.js", False),
    ]:
        called = []
        fake = SimpleNamespace(
            request=SimpleNamespace(method=method, url=url, resource_type="script"),
            abort=lambda: _record(called, "abort"),
            continue_=lambda: _record(called, "continue"),
        )
        await _guard_http(fake)
        assert called == ["abort" if should_block else "continue"]
    actions = []
    await _block_socket(SimpleNamespace(close=lambda: _record(actions, "close")))
    assert actions == ["close"]


async def _record(target, value):
    target.append(value)


@pytest.mark.asyncio
async def test_browser_private_render_requires_shared_secret_before_execution():
    class Fake:
        def __init__(self):
            self.urls = []
        async def render(self, url):
            self.urls.append(url)
            return {"status": "opened", "url": url, "text": "Result"}

    renderer = Fake()
    app.state.secret = "y" * 40
    app.state.renderer = renderer
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            denied = await client.post("/render", json={"url": "https://example.org/"})
            assert denied.status_code == 403
            assert renderer.urls == []
            allowed = await client.post(
                "/render", json={"url": "https://example.org/"},
                headers={"X-Amora-Browser-Token": "y" * 40},
            )
            assert allowed.status_code == 200
            assert renderer.urls == ["https://example.org/"]
    finally:
        del app.state.secret
        del app.state.renderer


@pytest.mark.asyncio
async def test_core_bridge_sends_only_url_and_token_not_owner_memory(monkeypatch):
    observed = {}
    class FakeHTTP:
        def __init__(self, **kwargs):
            observed["config"] = kwargs
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        async def post(self, url, **kwargs):
            observed["url"] = url
            observed["request"] = kwargs
            return httpx.Response(200, json={
                "status": "opened", "url": "https://example.org/rendered",
                "title": "Article", "text": "JS article contents",
                "links": [{"url": "http://127.0.0.1/secret", "title": "private"}],
            })

    monkeypatch.setattr("amora.web_tools.browser_client.httpx.AsyncClient", FakeHTTP)
    token = "x" * 40
    page = await BrowserClient("https://worker.example.org", token).render(
        "https://example.org/request"
    )
    assert observed["request"]["json"] == {"url": "https://example.org/request"}
    assert observed["request"]["headers"] == {"X-Amora-Browser-Token": token}
    assert observed["config"]["trust_env"] is False
    assert observed["config"]["follow_redirects"] is False
    assert page["url"] == "https://example.org/rendered"
    assert page["links"] == []
    assert "x" * 20 not in str(page)


@pytest.mark.asyncio
async def test_core_tries_chromium_only_when_static_html_is_js_shell():
    class Browser:
        def __init__(self):
            self.urls = []
        async def render(self, url):
            self.urls.append(url)
            return {"status": "opened", "url": url, "title": "Rendered",
                    "text": "Uma matéria real carregada via Javascript",
                    "links": [{"url": "https://example.org/next", "title": "Next"}],
                    "fetched_at": "2026-11-20T16:00:00+00:00"}
    browser = Browser()
    visits = []

    def fake_fetch(url):
        visits.append(url)
        return {"status": "opened", "url": url, "title": "Loading",
                "text": "Enable JavaScript to continue", "links": [],
                "fetched_at": "2026-11-20T15:00:00+00:00"}

    navigator = WebNavigator(fetch_page=fake_fetch, browser=browser)
    result = await navigator.research("abre https://example.org/with-js", "dm")
    assert result["status"] == "opened"
    assert result["sources"][0]["text"].startswith("Uma matéria real")
    assert browser.urls == ["https://example.org/with-js"]
    followed = await navigator.research("abre o link 1", "dm")
    assert followed["sources"][0]["url"] == "https://example.org/next"
    assert browser.urls[-1] == "https://example.org/next"
    assert len(visits) == 2


@pytest.mark.asyncio
async def test_core_does_not_launch_browser_for_full_static_article():
    class Browser:
        async def render(self, url):
            raise AssertionError("unnecessary Chromium launch")
    long_text = "A matéria foi lida com sucesso. " * 30
    navigator = WebNavigator(
        fetch_page=lambda url: {
            "status": "opened", "url": url, "title": "Static",
            "text": long_text, "fetched_at": "2026-11-20T12:00:00+00:00",
            "links": [],
        },
        browser=Browser(),
    )
    result = await navigator.research("https://example.org/full", "dm")
    assert result["sources"][0]["text"] == long_text


@pytest.mark.asyncio
async def test_core_preserves_http_error_if_browser_fails():
    async def blocked(url):
        raise WebFetchError("WEB_BROWSER_UNAVAILABLE")
    class FakeBrowser:
        render = blocked
    def static_error(url):
        raise WebFetchError("WEB_HTTP_403")
    navigator = WebNavigator(fetch_page=static_error, browser=FakeBrowser())
    result = await navigator.research("https://example.org/blocked", "dm")
    assert result["status"] == "failed"
    assert result["reason"] == "WEB_HTTP_403"
    assert result["sources"] == []
