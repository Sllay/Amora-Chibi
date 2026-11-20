"""Run 2 low-memory remote-renderer tests; strictly no external network."""
from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from amora.web_tools.navigator import WebFetchError, WebNavigator
from amora.web_tools.remote_reader import (
    RemoteReader, parse_reader_output, _shareable_public_url,
)

ARTICLE = (
    "Title: Godot release notes\n"
    "URL Source: https://godotengine.org/article/releases/\n"
    "Markdown Content:\n"
    "# Godot release notes\n"
    "A long page published by the official project. " * 9
    + "\n[Read full details](https://godotengine.org/releases/)\n"
)


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/private",
    "http://169.254.169.254/latest/meta-data/",
    "http://localhost/admin",
    "https://example.org/?access_token=secret",
    "https://example.org/?api_key=abc123",
    "https://example.org/?sig=abcdef",
    "https://name:pass@example.org/",
])
def test_remote_reader_will_not_forward_private_or_credential_urls(url):
    with pytest.raises(WebFetchError):
        _shareable_public_url(url)


def test_reader_envelope_extracts_real_source_and_source_links():
    result = parse_reader_output(ARTICLE, "https://godotengine.org/article/releases/")
    assert result["status"] == "opened"
    assert result["retrieval"] == "third_party_jina_reader"
    assert result["title"] == "Godot release notes"
    assert result["url"] == "https://godotengine.org/article/releases/"
    assert result["links"] == [{
        "url": "https://godotengine.org/releases/", "title": "Read full details"
    }]


def test_external_vendor_cannot_change_cited_original_url():
    injected = ARTICLE.replace(
        "URL Source: https://godotengine.org/article/releases/",
        "URL Source: https://attacker.invalid/fake",
    )
    result = parse_reader_output(injected, "https://godotengine.org/article/releases/")
    assert result["url"] == "https://godotengine.org/article/releases/"


@pytest.mark.parametrize("body", [
    "An unexpected reader error with 200 HTTP",
    "Title: Empty\nURL Source: https://example.org\nMarkdown Content:\n",
    "Title: Captcha\nURL Source: https://example.org\nMarkdown Content:\n"
    + "Verify you are human, captcha required. " * 8,
])
def test_reader_fails_closed_on_error_or_interstitial(body):
    with pytest.raises(WebFetchError):
        parse_reader_output(body, "https://example.org/")


@pytest.mark.asyncio
async def test_reader_no_key_sends_only_public_url_and_fixed_header(monkeypatch):
    observed = {}
    class Reply:
        status_code = 200
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        async def aiter_bytes(self):
            yield ARTICLE.encode("utf-8")

    class Client:
        def __init__(self, **kwargs):
            observed["kwargs"] = kwargs
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def stream(self, method, url, **kwargs):
            observed["method"] = method
            observed["url"] = url
            observed["headers"] = kwargs.get("headers")
            return Reply()

    monkeypatch.setattr("amora.web_tools.remote_reader.httpx.AsyncClient", Client)
    reader = RemoteReader()
    page = await reader.render("https://godotengine.org/article/releases/")
    assert page["status"] == "opened"
    assert observed["method"] == "GET"
    assert observed["url"] == (
        "https://r.jina.ai/https://godotengine.org/article/releases/"
    )
    assert observed["headers"]["X-Engine"] == "browser"
    assert observed["kwargs"]["trust_env"] is False
    assert observed["kwargs"]["follow_redirects"] is False
    assert "authorization" not in str(observed["headers"]).lower()
    assert "memory" not in str(observed).lower()


@pytest.mark.asyncio
async def test_reader_rate_limit_remains_unavailable_not_fake_content(monkeypatch):
    class Reply:
        status_code = 429
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
    class Client:
        def __init__(self, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            pass
        def stream(self, *args, **kwargs):
            return Reply()
    monkeypatch.setattr("amora.web_tools.remote_reader.httpx.AsyncClient", Client)
    with pytest.raises(WebFetchError) as ex:
        await RemoteReader().render("https://example.org/article")
    assert ex.value.code == "WEB_EXTERNAL_READER_RATE_LIMIT"


@pytest.mark.asyncio
async def test_browser_offload_only_on_js_shell_and_keeps_source_provenance():
    class FakeReader:
        def __init__(self):
            self.urls = []
        async def render(self, url):
            self.urls.append(url)
            return {
                "status": "opened", "url": url, "title": "JS page",
                "text": "A real dynamically-rendered article from remote browsing.",
                "links": [{"url": "https://example.org/next", "title": "Next"}],
                "fetched_at": "2026-11-20T12:30:00+00:00",
                "retrieval": "third_party_jina_reader",
            }
    reader = FakeReader()
    def static(url):
        return {
            "status": "opened", "url": url, "title": "Loading",
            "text": "Please enable javascript to view this page",
            "links": [], "fetched_at": "2026-11-20T12:00:00+00:00",
        }
    navigator = WebNavigator(fetch_page=static, reader=reader)
    outcome = await navigator.research("https://example.org/js", "owner-dm")
    assert outcome["sources"][0]["retrieval"] == "third_party_jina_reader"
    assert outcome["sources"][0]["url"] == "https://example.org/js"
    assert reader.urls == ["https://example.org/js"]
    followed = await navigator.research("abre o link 1", "owner-dm")
    assert followed["sources"][0]["url"] == "https://example.org/next"


@pytest.mark.asyncio
async def test_full_static_page_uses_no_remote_service():
    class NeverRemote:
        async def render(self, url):
            raise AssertionError("Remote browser should not be called")
    navigator = WebNavigator(
        fetch_page=lambda url: {
            "status": "opened", "url": url, "title": "Full static",
            "text": "Conteúdo do artigo legítimo. " * 30,
            "links": [], "fetched_at": "2026-11-20T12:30:00+00:00",
        },
        reader=NeverRemote(),
    )
    result = await navigator.research("https://example.org/static", "dm")
    assert result["status"] == "opened"
    assert result["sources"][0]["text"].startswith("Conteúdo do artigo")


@pytest.mark.asyncio
async def test_failed_external_reader_does_not_override_direct_http_error():
    class Refuses:
        async def render(self, url):
            raise WebFetchError("WEB_EXTERNAL_READER_UNAVAILABLE")
    def static_403(url):
        raise WebFetchError("WEB_HTTP_403")
    navigator = WebNavigator(fetch_page=static_403, reader=Refuses())
    result = await navigator.research("https://example.org/blocked", "owner-dm")
    assert result["status"] == "failed"
    assert result["reason"] == "WEB_HTTP_403"
    assert not result["sources"]


def test_remote_renderer_defaults_on_but_can_be_disabled(monkeypatch):
    monkeypatch.delenv("AMORA_WEB_REMOTE_READER", raising=False)
    assert RemoteReader.from_environment() is not None
    monkeypatch.setenv("AMORA_WEB_REMOTE_READER", "0")
    assert RemoteReader.from_environment() is None
