"""Authenticated read-only Chromium render API for Amora's private web worker.

Deliberately independent of the Amora Core: every request uses an ephemeral
browser context with no owner credentials. Browser network travels through
the pinned-DNS proxy, with Chromium user sandbox and policy-level request
guards. It must run in a separate non-root container and should additionally
have external-only egress enforced by the platform's network policy.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timezone
import hmac
import hashlib
import logging
import ipaddress
import os
import re
from typing import Any
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel, Field

from .proxy import PinnedProxy, ProxyDenied, validate_authority

MAX_TEXT = 2400
MAX_LINKS = 12
MAX_URL = 2048
MAX_PAGE_SECONDS = 10.0
MAX_RESPONSE_BODY = 2600
MAX_CAPTURE_BYTES = 2_500_000
_LOG = logging.getLogger("amora.browser")


class RenderRequest(BaseModel):
    url: str = Field(min_length=9, max_length=MAX_URL)


def checked_web_url(value: str) -> str:
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise ProxyDenied("URL_INVALID")
    p = urlsplit(value)
    if p.scheme not in ("http", "https") or not p.hostname:
        raise ProxyDenied("URL_INVALID")
    if p.username is not None or p.password is not None or "@" in p.netloc or "%" in p.netloc or "\\" in p.netloc:
        raise ProxyDenied("URL_INVALID")
    try:
        validate_authority(p.hostname, p.port or (443 if p.scheme == "https" else 80))
    except (ValueError, OverflowError) as exc:
        raise ProxyDenied("URL_INVALID") from exc
    # Explicitly disallow HTML embedded URLs, file:, WebSocket and chrome:.
    return value.split("#", 1)[0]


EXTRACT_JS = r"""() => {
    const keep = (value, limit) => String(value || '')
      .replace(/\s+/g, ' ').trim().slice(0, limit);
    const containers = Array.from(document.querySelectorAll(
      'main, article, [role="main"], .post-content, .article-body'));
    let target = containers.find(e => e.innerText && e.innerText.trim().length > 80)
      || document.body;
    if (!target) return {title: '', text: '', links: []};
    const clone = target.cloneNode(true);
    clone.querySelectorAll(
      'script, style, noscript, svg, nav, header, footer, aside, form, button'
    ).forEach(el => el.remove());
    const text = keep(clone.innerText || clone.textContent, 2400);
    const links = [];
    const seen = new Set();
    for (const anchor of target.querySelectorAll('a[href]')) {
      const title = keep(anchor.innerText || anchor.getAttribute('aria-label'), 160);
      const href = anchor.href;
      if (title && /^https?:\/\//i.test(href) && !seen.has(href)) {
        seen.add(href);
        links.push({url: href, title});
      }
      if (links.length >= 36) break;
    }
    return {title: keep(document.title, 180), text, links};
}"""


async def _guard_http(route, *, capture: bool = False) -> None:
    request = route.request
    try:
        checked_web_url(request.url)
        if request.method.upper() not in {"GET", "HEAD"}:
            await route.abort()
            return
        blocked = {"media", "font", "manifest", "beacon"}
        if not capture:
            blocked.add("image")
        if request.resource_type in blocked:
            await route.abort()
            return
    except (ProxyDenied, ValueError):
        await route.abort()
        return
    await route.continue_()


async def _block_socket(socket_route) -> None:
    # No page-generated WebSocket data tunnels, even on loopback bypass.
    await socket_route.close()


class BrowserRenderer:
    def __init__(self, browser, proxy_url: str):
        self.browser = browser
        self.proxy_url = proxy_url
        self._render_lock = asyncio.Lock()

    async def render(self, url: str, *, capture: bool = False) -> dict[str, Any] | bytes:
        checked = checked_web_url(url)
        # Fail fast on contention: the worker has precisely one Chromium slot.
        try:
            await asyncio.wait_for(self._render_lock.acquire(), timeout=0.15)
        except TimeoutError as exc:
            raise ProxyDenied("BROWSER_BUSY") from exc
        context = None
        try:
            context = await self.browser.new_context(
                proxy={"server": self.proxy_url, "bypass": ""},
                accept_downloads=False,
                service_workers="block",
                ignore_https_errors=False,
                java_script_enabled=True,
                locale="pt-BR",
                permissions=[],
                viewport={"width": 900, "height": 700},
            )
            if capture:
                async def guard(route):
                    await _guard_http(route, capture=True)
                await context.route("**/*", guard)
            else:
                await context.route("**/*", _guard_http)
            await context.route_web_socket("**/*", _block_socket)
            page = await context.new_page()
            page.set_default_timeout(3500)
            page.set_default_navigation_timeout(8000)
            response = await page.goto(checked, wait_until="domcontentloaded", timeout=8000)
            await page.wait_for_timeout(650)
            if response is None:
                raise ProxyDenied("BROWSER_NO_RESPONSE")
            status = response.status
            if status not in range(200, 300):
                raise ProxyDenied("BROWSER_HTTP_ERROR")
            final_url = checked_web_url(page.url)
            details = await asyncio.wait_for(page.evaluate(EXTRACT_JS), timeout=2.0)
            if not isinstance(details, dict):
                raise ProxyDenied("BROWSER_EXTRACTION_FAILED")
            links = []
            for candidate in details.get("links", [])[:36]:
                try:
                    href = checked_web_url(str(candidate.get("url", "")))
                except (ProxyDenied, ValueError, OverflowError):
                    continue
                if href == final_url or any(link["url"] == href for link in links):
                    continue
                links.append({"url": href, "title": str(candidate.get("title", ""))[:160]})
                if len(links) >= MAX_LINKS:
                    break
            text = str(details.get("text") or "")[:MAX_TEXT]
            challenge = text.strip().casefold()
            # WAF/CAPTCHA interstitials are not real evidence that the
            # requested article or page was read successfully.
            if len(challenge) < 350 and any(cue in challenge for cue in (
                "just a moment", "verify you are human", "checking your browser",
                "enable cookies", "captcha", "verifique que você é humano",
            )):
                raise ProxyDenied("BROWSER_ACCESS_CHALLENGE")
            if capture:
                data = await page.screenshot(type="png", full_page=False,
                                             animations="disabled", timeout=2500)
                if not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > MAX_CAPTURE_BYTES:
                    raise ProxyDenied("BROWSER_CAPTURE_INVALID")
                return data
            return {
                "status": "opened" if text.strip() else "empty",
                "url": final_url,
                "title": str(details.get("title") or "")[:180],
                "text": text,
                "links": links,
                "fetched_at": datetime.now(timezone.utc).isoformat(),
                "content_type": "text/html",
                "render_mode": "chromium_js_v1",
            }
        finally:
            try:
                if context is not None:
                    await context.close()
            finally:
                self._render_lock.release()


@asynccontextmanager
async def lifespan(app: FastAPI):
    secret = os.environ.get("AMORA_WEB_BROWSER_TOKEN", "")
    if len(secret) < 32:
        raise RuntimeError("AMORA_WEB_BROWSER_TOKEN must be at least 32 characters")
    from playwright.async_api import async_playwright

    proxy = await PinnedProxy().start()
    playwright = await async_playwright().start()
    browser = None
    try:
        browser = await playwright.chromium.launch(
            headless=True,
            chromium_sandbox=True,
            args=[
                "--disable-quic",
                "--proxy-bypass-list=<-loopback>",
                "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
                "--disable-background-networking",
                "--disable-features=MediaRouter",
            ],
        )
        app.state.renderer = BrowserRenderer(browser, proxy.url)
        app.state.secret = secret
        yield
    finally:
        if browser is not None:
            await browser.close()
        await playwright.stop()
        await proxy.close()


app = FastAPI(
    title="Amora Browser Worker (private only)", docs_url=None,
    redoc_url=None, openapi_url=None, lifespan=lifespan,
)


@app.middleware("http")
async def reject_unauthorized_render_before_parsing_body(request: Request, call_next):
    if request.url.path in {"/render", "/capture"}:
        token = request.headers.get("x-amora-browser-token", "")
        secret = getattr(app.state, "secret", "")
        if not secret or not token or not hmac.compare_digest(token, secret):
            return Response(status_code=403)
        declared = request.headers.get("content-length", "")
        if not declared.isdigit() or int(declared) > 4096:
            return Response(status_code=413)
        if not request.headers.get("content-type", "").startswith("application/json"):
            return Response(status_code=415)
    return await call_next(request)


@app.get("/health")
async def health():
    return {"status": "ready" if hasattr(app.state, "renderer") else "unavailable",
            "render": True, "capture": os.getenv("AMORA_WEB_BROWSER_CAPTURE_ENABLED") == "1"}


@app.post("/render")
async def render(body: RenderRequest, x_amora_browser_token: str | None = Header(default=None)):
    secret = getattr(app.state, "secret", "")
    if not secret or not x_amora_browser_token or not hmac.compare_digest(
        x_amora_browser_token, secret
    ):
        raise HTTPException(status_code=403, detail="BROWSER_AUTH_DENIED")
    try:
        return await asyncio.wait_for(
            app.state.renderer.render(body.url), timeout=MAX_PAGE_SECONDS
        )
    except ProxyDenied as exc:
        raise HTTPException(status_code=422, detail=exc.code) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="BROWSER_TIMEOUT") from exc
    except Exception as exc:
        # Never leak page URL, browser internals, cookies or HTML in diagnostics.
        raise HTTPException(status_code=502, detail="BROWSER_RENDER_FAILED") from exc

@app.post("/capture")
async def capture_page(body: RenderRequest, x_amora_browser_token: str | None = Header(default=None),
                       x_amora_capture_id: str | None = Header(default=None)):
    secret = getattr(app.state, "secret", "")
    if not secret or not x_amora_browser_token or not hmac.compare_digest(x_amora_browser_token, secret):
        raise HTTPException(status_code=403, detail="BROWSER_AUTH_DENIED")
    if os.getenv("AMORA_WEB_BROWSER_CAPTURE_ENABLED") != "1":
        raise HTTPException(status_code=503, detail="BROWSER_CAPTURE_DISABLED")
    correlation = x_amora_capture_id if re.fullmatch(r"[a-f0-9]{24}", x_amora_capture_id or "") else "-"
    target_hash = hashlib.sha256(body.url.encode()).hexdigest()[:24]
    started = asyncio.get_running_loop().time()
    try:
        # Same sandbox, pinned DNS proxy, one slot and ephemeral context as /render.
        data = await asyncio.wait_for(app.state.renderer.render(body.url, capture=True), timeout=12)
        if not isinstance(data, bytes) or not data.startswith(b"\x89PNG\r\n\x1a\n") or len(data) > MAX_CAPTURE_BYTES:
            raise ProxyDenied("BROWSER_CAPTURE_INVALID")
        _LOG.info("capture_id=%s target_hash=%s stage=capture_ready bytes=%d ms=%d",
                  correlation, target_hash, len(data), round((asyncio.get_running_loop().time()-started)*1000))
        return Response(content=data, media_type="image/png", headers={"Cache-Control": "no-store"})
    except ProxyDenied as exc:
        _LOG.info("capture_id=%s target_hash=%s stage=nav_or_capture_failed code=%s",
                  correlation, target_hash, exc.code)
        raise HTTPException(status_code=422, detail=exc.code) from exc
    except TimeoutError as exc:
        raise HTTPException(status_code=504, detail="BROWSER_TIMEOUT") from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail="BROWSER_CAPTURE_FAILED") from exc

