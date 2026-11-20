"""Optional remote JavaScript rendering, without any browser in Amora Core.

Reader runs its rendering engine remotely. Only an explicit, public URL is
forwarded (never conversation history, Amora memories, identity or cookies).
This is a fallible third-party retrieval, not a universal site-access grant.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import os
import re
from urllib.parse import parse_qsl, urljoin, urlsplit

import httpx

from .navigator import MAX_SOURCE_TEXT, WebFetchError, _clean, _public_url

SENSITIVE_QUERY_FIELDS = frozenset({
    "token", "access_token", "api_key", "apikey", "key",
    "auth", "authorization", "password", "passwd", "secret",
    "signature", "sig", "session", "sessionid", "code", "jwt",
})
MARKDOWN_LINK = re.compile(r"\[([^\]\n]{2,120})\]\((https?://[^\s)]+)\)", re.I)
TITLE = re.compile(r"(?m)^Title:\s*(.*?)\s*$")
SOURCE = re.compile(r"(?m)^URL Source:\s*(https?://\S+)\s*$")
BODY_START = re.compile(r"(?mi)^Markdown Content:\s*$")


def _shareable_public_url(url: str) -> str:
    safe = _public_url(url)[0]
    parsed = urlsplit(safe)
    if any(k.casefold() in SENSITIVE_QUERY_FIELDS for k, _ in parse_qsl(
        parsed.query, keep_blank_values=True
    )):
        # Do not forward credential-like link parameters to a third party.
        raise WebFetchError("WEB_EXTERNAL_READER_SENSITIVE_URL")
    return safe


def parse_reader_output(body: str, original_url: str) -> dict[str, object]:
    if not isinstance(body, str) or not body.strip():
        raise WebFetchError("WEB_EXTERNAL_READER_EMPTY")
    original = _shareable_public_url(original_url)
    source = SOURCE.search(body[:3500])
    if source:
        resolved = _public_url(source.group(1))[0]
    else:
        resolved = original
    title_match = TITLE.search(body[:3500])
    separator = BODY_START.search(body)
    text = body[separator.end():].strip() if separator else body.strip()
    text = _clean(text, MAX_SOURCE_TEXT)
    challenge = text.casefold()[:400]
    if len(text) < 80 or any(code in challenge for code in (
        "just a moment", "verify you are human", "checking your browser",
        "access denied", "rate limit exceeded", "enable javascript",
        "captcha", "challenge required",
    )):
        raise WebFetchError("WEB_EXTERNAL_READER_NO_CONTENT")
    links = []
    for item in MARKDOWN_LINK.finditer(body[:90000]):
        try:
            url = _public_url(item.group(2))[0]
        except WebFetchError:
            continue
        if url != resolved and not any(x["url"] == url for x in links):
            links.append({"url": url, "title": _clean(item.group(1), 120)})
        if len(links) >= 12:
            break
    return {
        "status": "opened", "url": resolved,
        "title": _clean(title_match.group(1) if title_match else urlsplit(resolved).hostname or "", 180),
        "text": text, "links": links,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "content_type": "text/markdown",
        "retrieval": "third_party_jina_reader",
    }


class RemoteReader:
    """A lightweight HTTP client. It never launches or embeds Chromium."""

    def __init__(self, *, enabled: bool = True):
        self.enabled = enabled
        self._slots = asyncio.Semaphore(1)

    @classmethod
    def from_environment(cls):
        value = os.getenv("AMORA_WEB_REMOTE_READER", "1").strip().lower()
        return cls() if value not in {"0", "false", "off"} else None

    async def render(self, url: str) -> dict[str, object]:
        if not self.enabled:
            raise WebFetchError("WEB_EXTERNAL_READER_DISABLED")
        safe_url = _shareable_public_url(url)
        try:
            await asyncio.wait_for(self._slots.acquire(), timeout=0.25)
        except TimeoutError as exc:
            raise WebFetchError("WEB_EXTERNAL_READER_BUSY") from exc
        try:
            async with httpx.AsyncClient(
                timeout=httpx.Timeout(9.0, connect=2.5),
                trust_env=False, follow_redirects=False,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            ) as client:
                # Reader's Chromium is hosted by Jina, NOT in our server.
                # The API is keyless for basic public URL reads. A 429,
                # 403 or other provider refusal must be an honest failure.
                async with client.stream(
                    "GET", "https://r.jina.ai/" + safe_url,
                    headers={
                        "Accept": "text/plain",
                        "X-Engine": "browser",
                        "X-Respond-Timing": "visible-content",
                        "X-Max-Tokens": "3000",
                    },
                ) as response:
                    if response.status_code != 200:
                        raise WebFetchError(
                            "WEB_EXTERNAL_READER_RATE_LIMIT"
                            if response.status_code == 429
                            else "WEB_EXTERNAL_READER_UNAVAILABLE"
                        )
                    data = bytearray()
                    async for chunk in response.aiter_bytes():
                        data.extend(chunk)
                        if len(data) > 96_000:
                            raise WebFetchError("WEB_EXTERNAL_READER_BODY_TOO_LARGE")
            return parse_reader_output(data.decode("utf-8", errors="replace"), safe_url)
        except WebFetchError:
            raise
        except (httpx.HTTPError, ValueError, UnicodeError) as exc:
            raise WebFetchError("WEB_EXTERNAL_READER_UNAVAILABLE") from exc
        finally:
            self._slots.release()
