"""Optional authenticated bridge to the *separate* Chromium worker.

Core has no Playwright/Chromium dependency and never launches browser
processes. This transport does not send conversation history, owner identity,
credentials, memories or prompt to the worker -- only one public URL.
"""
from __future__ import annotations

import os
from urllib.parse import urlsplit

import httpx

from .navigator import WebFetchError, _clean, _public_url


class BrowserClient:
    def __init__(self, endpoint: str, token: str):
        self.endpoint = endpoint.strip().rstrip("/")
        self.token = token.strip()
        parsed = urlsplit(self.endpoint)
        if (
            not self.token or len(self.token) < 32 or not parsed.hostname
            or parsed.query or parsed.fragment or parsed.username or parsed.password
            or parsed.path not in ("", "/")
        ):
            raise WebFetchError("WEB_BROWSER_CONFIG_INVALID")
        if parsed.scheme != "https":
            # Northflank/private service traffic may explicitly opt into HTTP.
            # Not permitted by default on public network addresses.
            if not (parsed.scheme == "http"
                    and os.getenv("AMORA_WEB_BROWSER_PRIVATE_HTTP") == "1"):
                raise WebFetchError("WEB_BROWSER_CONFIG_INVALID")

    @classmethod
    def from_environment(cls):
        url = os.getenv("AMORA_WEB_BROWSER_URL", "").strip()
        token = os.getenv("AMORA_WEB_BROWSER_TOKEN", "")
        if not url or not token:
            return None
        try:
            return cls(url, token)
        except WebFetchError:
            return None

    async def render(self, url: str) -> dict[str, object]:
        safe, _, _, _ = _public_url(url)
        try:
            async with httpx.AsyncClient(
                trust_env=False,
                timeout=httpx.Timeout(11.0, connect=3.0),
                follow_redirects=False,
                limits=httpx.Limits(max_connections=1, max_keepalive_connections=0),
            ) as client:
                response = await client.post(
                    self.endpoint + "/render",
                    json={"url": safe},
                    headers={"X-Amora-Browser-Token": self.token},
                )
            if response.status_code != 200:
                raise WebFetchError(
                    "WEB_BROWSER_BUSY" if response.status_code == 422
                    else "WEB_BROWSER_UNAVAILABLE"
                )
            if len(response.content) > 24_000:
                raise WebFetchError("WEB_BROWSER_RESULT_TOO_LARGE")
            doc = response.json()
            if not isinstance(doc, dict) or doc.get("status") not in {"opened", "empty"}:
                raise WebFetchError("WEB_BROWSER_RESULT_INVALID")
            final_url = _public_url(str(doc.get("url", "")))[0]
            links = []
            for item in doc.get("links", [])[:24]:
                if not isinstance(item, dict):
                    continue
                try:
                    link = _public_url(str(item.get("url", "")))[0]
                except WebFetchError:
                    continue
                if link != final_url and not any(existing["url"] == link for existing in links):
                    links.append({"url": link, "title": _clean(str(item.get("title", "")), 160)})
                if len(links) >= 12:
                    break
            text = _clean(str(doc.get("text") or ""), 2400)
            return {
                "status": "opened" if doc["status"] == "opened" and text else "empty",
                "url": final_url,
                "title": _clean(str(doc.get("title") or ""), 180),
                "text": text, "links": links,
                "fetched_at": str(doc.get("fetched_at") or "")[:40],
                "content_type": "text/html", "render_mode": "chromium_js_v1",
            }
        except WebFetchError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise WebFetchError("WEB_BROWSER_UNAVAILABLE") from exc
