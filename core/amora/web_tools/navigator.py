"""Source-grounded web.open/web.search/web.follow/web.extract, no paid API keys.

Public HTTP only: every DNS answer is checked and the socket uses a pinned
approved address, preserving original Host and TLS/SNI certificate checks.
Redirects are independently revalidated. No cookie/session/proxy forwarding,
scripting, external commands, file access, or site-name allowlist.

This is a *read-only* explicit-intent tool. Web material is untrusted and must
never become an identity, policy, instruction or durable fact by itself.
"""
from __future__ import annotations

import asyncio
import html
from html.parser import HTMLParser
import http.client
import ipaddress
import json
import os
import re
import socket
import ssl
import xml.etree.ElementTree as ET
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qs, quote, quote_plus, unquote, urljoin, urlsplit, urlunsplit

MAX_URL_LENGTH = 2048
MAX_BYTES = 420_000
MAX_SOURCE_TEXT = 2400
MAX_HOPS = 3
ALLOWED_PORTS = frozenset({80, 443, 8080, 8443})
USER_AGENT = "AmoraWeb/1.0 (read-only source retrieval; no automated login)"

_LINK_RE = re.compile(r"https?://[^\s<>\"'\x60]+", re.IGNORECASE)
_FOLLOW_RE = re.compile(
    r"\b(?:abre|abra|abrir|acessa|acesse|segue|seguir|leia|l[eê])\b"
    r".{0,35}?\b(?:link|resultado|refer[eê]ncia)\s*(?:n[ºo°.]?\s*)?(\d{1,2})\b",
    re.IGNORECASE | re.DOTALL,
)
_SEARCH_RE = re.compile(
    r"\b(?:pesquis[ae]|pesquise|buscar?|busque|procur[ae]|procure|"
    r"investig[ae]|investigue|busca\s+na\s+(?:web|internet)|"
    r"veja\s+na\s+(?:web|internet)|pesquisa\s+na\s+(?:web|internet))\b",
    re.IGNORECASE,
)


class WebFetchError(RuntimeError):
    def __init__(self, code: str):
        super().__init__(code)
        self.code = code


def _public_url(url: str) -> tuple[str, str, int, str]:
    value = str(url).strip()
    if not value or len(value) > MAX_URL_LENGTH or any(ord(ch) < 32 for ch in value):
        raise WebFetchError("WEB_URL_INVALID")
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"} or not parsed.hostname:
            raise ValueError
        if parsed.username is not None or parsed.password is not None:
            raise ValueError
        if "@" in parsed.netloc or "%" in parsed.netloc or "\\" in parsed.netloc:
            raise ValueError
        port = parsed.port or (443 if scheme == "https" else 80)
        if port not in ALLOWED_PORTS:
            raise WebFetchError("WEB_PORT_NOT_SUPPORTED")
        host = parsed.hostname.encode("idna").decode("ascii").lower()
        if not host or len(host) > 253 or host.endswith((".local", ".localhost", ".internal", ".onion")):
            raise ValueError
        if host in {"localhost", "metadata.google.internal"} or host.endswith("."):
            raise ValueError
        # Reject literal private/reserved addresses even in search-result
        # metadata; DNS-name targets are rechecked and pinned at connection time.
        try:
            literal_ip = ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            if isinstance(literal_ip, ipaddress.IPv6Address) and literal_ip.ipv4_mapped:
                literal_ip = literal_ip.ipv4_mapped
            if not literal_ip.is_global:
                raise WebFetchError("WEB_PRIVATE_NETWORK_DENIED")
        safe_path = quote(parsed.path or "/", safe="/%:@!$&'()*+,;=-._~")
        safe_query = quote(parsed.query, safe="=&%:/?+;,@!$'()*-._~")
        ascii_host = f"[{host}]" if ":" in host else host
        netloc = ascii_host + (f":{port}" if port != (443 if scheme == "https" else 80) else "")
        # Unicode paths and IDNA hostnames remain navigable without changing
        # the certificate hostname, and fragments are never transmitted.
        result = urlunsplit((scheme, netloc, safe_path, safe_query, ""))
    except WebFetchError:
        raise
    except (ValueError, UnicodeError, OverflowError) as exc:
        raise WebFetchError("WEB_URL_INVALID") from exc
    return result, host, port, scheme


def _approved_ip(host: str, port: int) -> str:
    try:
        addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except (socket.gaierror, OSError) as exc:
        raise WebFetchError("WEB_DNS_FAILED") from exc
    if not addresses:
        raise WebFetchError("WEB_DNS_FAILED")
    allowed: list[str] = []
    # Fail closed on *mixed* public/private DNS answers. This prevents DNS
    # rebinding / internal network targets even if the first A record is public.
    for address in addresses:
        value = address[4][0]
        try:
            parsed = ipaddress.ip_address(value)
            if isinstance(parsed, ipaddress.IPv6Address) and parsed.ipv4_mapped:
                parsed = parsed.ipv4_mapped
            if not parsed.is_global:
                raise WebFetchError("WEB_PRIVATE_NETWORK_DENIED")
        except ValueError as exc:
            raise WebFetchError("WEB_DNS_FAILED") from exc
        allowed.append(str(parsed))
    return allowed[0]


def _fetch_once(url: str) -> tuple[int, dict[str, str], bytes]:
    safe, host, port, scheme = _public_url(url)
    ip = _approved_ip(host, port)
    if scheme == "https":
        conn = http.client.HTTPSConnection(host, port, timeout=4, context=ssl.create_default_context())
    else:
        conn = http.client.HTTPConnection(host, port, timeout=4)
    # HTTPConnection normally performs a second DNS lookup in connect(). Pin
    # its actual socket to the *validated* IP without changing TLS SNI/hostname.
    conn._create_connection = (
        lambda address, timeout, source_address=None:
        socket.create_connection((ip, port), timeout, source_address)
    )
    parsed = urlsplit(safe)
    path = parsed.path + (("?" + parsed.query) if parsed.query else "")
    try:
        conn.request("GET", path, headers={
            "Host": parsed.netloc,
            "User-Agent": USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/json,text/plain,application/xml,text/xml,*/*;q=0.2",
            "Accept-Encoding": "identity",
            "Connection": "close",
        })
        response = conn.getresponse()
        length = response.getheader("Content-Length")
        if length is not None:
            try:
                if int(length) > MAX_BYTES:
                    raise WebFetchError("WEB_BODY_TOO_LARGE")
            except ValueError:
                pass
        content = response.read(MAX_BYTES + 1)
        if len(content) > MAX_BYTES:
            raise WebFetchError("WEB_BODY_TOO_LARGE")
        headers = {
            key.lower(): value
            for key, value in response.getheaders()
            if key.lower() in {"content-type", "content-encoding", "location"}
        }
        return response.status, headers, content
    except WebFetchError:
        raise
    except (OSError, TimeoutError, ssl.SSLError, http.client.HTTPException) as exc:
        raise WebFetchError("WEB_FETCH_FAILED") from exc
    finally:
        conn.close()


def _http_get(url: str) -> tuple[str, int, dict[str, str], bytes]:
    current, _, _, _ = _public_url(url)
    visited: set[str] = set()
    for _ in range(MAX_HOPS + 1):
        if current in visited:
            raise WebFetchError("WEB_REDIRECT_LOOP")
        visited.add(current)
        status, headers, body = _fetch_once(current)
        if status in {301, 302, 303, 307, 308}:
            location = headers.get("location")
            if not location:
                raise WebFetchError("WEB_REDIRECT_INVALID")
            current, _, _, _ = _public_url(urljoin(current, location))
            continue
        if status != 200:
            raise WebFetchError(f"WEB_HTTP_{status}" if status in {401, 403, 404, 429, 500, 502, 503} else "WEB_HTTP_ERROR")
        if headers.get("content-encoding", "identity").lower() not in {"", "identity"}:
            raise WebFetchError("WEB_ENCODING_UNSUPPORTED")
        return current, status, headers, body
    raise WebFetchError("WEB_REDIRECT_LIMIT")


def _decode(payload: bytes, content_type: str) -> str:
    charset = "utf-8"
    match = re.search(r"charset\s*=\s*([\w.-]+)", content_type, re.IGNORECASE)
    if match:
        charset = match.group(1).lower()
    if charset not in {"utf-8", "utf8", "iso-8859-1", "latin-1", "windows-1252", "us-ascii"}:
        charset = "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _clean(value: str, maximum: int) -> str:
    return re.sub(r"\s+", " ", html.unescape(value)).strip()[:maximum]


class _HTMLExtractor(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template", "svg", "nav", "header", "footer", "aside", "form"})

    def __init__(self, base_url: str):
        super().__init__(convert_charrefs=True)
        self.base_url = base_url
        self.title = ""
        self._title_depth = 0
        self._skip_depth = 0
        self._main_depth = 0
        self._anchor: dict[str, Any] | None = None
        self.all_text: list[str] = []
        self.main_text: list[str] = []
        self.links: list[dict[str, str]] = []
        self.search_links: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        attrs_map = dict(attrs)
        if tag == "title":
            self._title_depth += 1
        if tag in self._SKIP:
            self._skip_depth += 1
        if tag in {"main", "article"}:
            self._main_depth += 1
        if tag == "a" and not self._skip_depth:
            href = attrs_map.get("href") or ""
            class_name = attrs_map.get("class") or ""
            self._anchor = {"url": urljoin(self.base_url, href), "text": [], "search": (
                "result__a" in class_name or "result-link" in class_name
            )}

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "a" and self._anchor is not None:
            url = self._anchor["url"]
            title = _clean(" ".join(self._anchor["text"]), 160)
            if title and url.startswith(("http://", "https://")) and len(self.links) < 80:
                found = {"url": url, "title": title}
                self.links.append(found)
                if self._anchor["search"]:
                    self.search_links.append(found)
            self._anchor = None
        if tag == "title" and self._title_depth:
            self._title_depth -= 1
        if tag in self._SKIP and self._skip_depth:
            self._skip_depth -= 1
        if tag in {"main", "article"} and self._main_depth:
            self._main_depth -= 1

    def handle_data(self, data: str) -> None:
        if self._title_depth and not self.title:
            self.title = _clean(data, 180)
        if self._anchor is not None:
            self._anchor["text"].append(data)
        if self._skip_depth or self._title_depth:
            return
        if data.strip():
            if len(self.all_text) < 6000:
                self.all_text.append(data)
            if self._main_depth and len(self.main_text) < 4000:
                self.main_text.append(data)


def _resolve_ddg_url(url: str) -> str:
    parsed = urlsplit(url)
    if parsed.hostname in {"duckduckgo.com", "www.duckduckgo.com"} and parsed.path.startswith("/l/"):
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        return unquote(target) if target.startswith(("http://", "https://")) else ""
    return url


@dataclass(frozen=True)
class WebIntent:
    action: str
    argument: str


def parse_web_intent(text: str) -> WebIntent | None:
    raw = str(text or "")[:12000]
    urls = _LINK_RE.findall(raw)
    if urls:
        first = urls[0].rstrip(".,;:!?)\u00bb")
        return WebIntent("open", first)
    follow = _FOLLOW_RE.search(raw)
    if follow:
        return WebIntent("follow", follow.group(1))
    search = _SEARCH_RE.search(raw)
    if search:
        # Keep the actual user query; no synthetic rewriting or broad external
        # search based merely on ordinary conversational references to the web.
        return WebIntent("search", raw[:350])
    return None


def fetch_public_page(url: str) -> dict[str, Any]:
    final_url, _, headers, body = _http_get(url)
    content_type = headers.get("content-type", "text/html")
    mime = content_type.split(";", 1)[0].strip().lower()
    fetched_at = datetime.now(timezone.utc).isoformat()
    if mime in {"text/html", "application/xhtml+xml", ""}:
        parser = _HTMLExtractor(final_url)
        parser.feed(_decode(body, content_type))
        text = _clean(" ".join(parser.main_text or parser.all_text), MAX_SOURCE_TEXT)
        links = []
        for item in parser.links:
            if len(links) >= 12:
                break
            if item["url"] != final_url and item not in links:
                links.append(item)
        return {
            "status": "opened" if text else "empty",
            "url": final_url,
            "title": parser.title or urlsplit(final_url).hostname,
            "text": text,
            "links": links,
            "fetched_at": fetched_at,
            "content_type": "text/html",
        }
    if mime in {"text/plain", "application/json", "text/xml", "application/xml", "application/rss+xml"}:
        content = _decode(body, content_type)
        return {
            "status": "opened", "url": final_url,
            "title": urlsplit(final_url).hostname,
            "text": _clean(content, MAX_SOURCE_TEXT),
            "links": [], "fetched_at": fetched_at, "content_type": mime,
        }
    return {
        "status": "unsupported", "url": final_url, "title": urlsplit(final_url).hostname,
        "text": "", "links": [], "fetched_at": fetched_at, "content_type": mime,
    }


def _searx_search(query: str, endpoint: str) -> list[dict[str, str]]:
    parsed = urlsplit(endpoint)
    if parsed.scheme != "https" or parsed.query or parsed.fragment or parsed.username:
        raise WebFetchError("WEB_SEARCH_ENDPOINT_INVALID")
    target = endpoint.rstrip("/") + "/search?q=" + quote_plus(query) + "&format=json&language=pt-BR"
    _, _, _, body = _http_get(target)
    try:
        root = json.loads(body)
        rows = root["results"]
        if not isinstance(rows, list):
            raise ValueError
    except (ValueError, KeyError, TypeError) as exc:
        raise WebFetchError("WEB_SEARCH_RESULT_INVALID") from exc
    results = []
    for item in rows[:20]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        try:
            url = _public_url(url)[0]
        except WebFetchError:
            continue
        results.append({"url": url, "title": _clean(str(item.get("title") or ""), 160),
                        "snippet": _clean(str(item.get("content") or ""), 300)})
        if len(results) >= 5:
            break
    return results


def _bing_rss_search(query: str) -> list[dict[str, str]]:
    """Keyless Bing web RSS search; malformed feeds fail closed."""
    target = "https://www.bing.com/search?format=rss&q=" + quote_plus(query)
    _, _, _, body = _http_get(target)
    # Refuse DTDs/entities before parsing XML controlled by a remote site.
    if b"<!DOCTYPE" in body.upper() or b"<!ENTITY" in body.upper():
        raise WebFetchError("WEB_SEARCH_RESULT_INVALID")
    try:
        root = ET.fromstring(body)
    except ET.ParseError as exc:
        raise WebFetchError("WEB_SEARCH_RESULT_INVALID") from exc
    results = []
    for item in root.findall("./channel/item")[:15]:
        url = str(item.findtext("link") or "")
        try:
            url = _public_url(url)[0]
        except WebFetchError:
            continue
        results.append({
            "url": url, "title": _clean(item.findtext("title") or "", 160),
            "snippet": _clean(item.findtext("description") or "", 300),
        })
        if len(results) >= 5:
            break
    if not results:
        raise WebFetchError("WEB_SEARCH_UNAVAILABLE")
    return results


def _ddg_search(query: str) -> list[dict[str, str]]:
    target = "https://html.duckduckgo.com/html/?q=" + quote_plus(query)
    final_url, _, headers, body = _http_get(target)
    parser = _HTMLExtractor(final_url)
    parser.feed(_decode(body, headers.get("content-type", "text/html")))
    results = []
    seen: set[str] = set()
    for item in parser.search_links:
        url = _resolve_ddg_url(item["url"])
        try:
            url = _public_url(url)[0]
        except WebFetchError:
            continue
        if url in seen:
            continue
        seen.add(url)
        results.append({"url": url, "title": item["title"], "snippet": ""})
        if len(results) >= 5:
            break
    if not results:
        raise WebFetchError("WEB_SEARCH_UNAVAILABLE")
    return results


class WebNavigator:
    """Ephemeral, bounded owner-DM navigation references and source evidence.

    Only the Core invokes this service after verifying the owner/DM provenance.
    Search results and page links grant no execution authority: following them
    always reuses the public-IP-pinned transport above.
    """

    def __init__(self, *, fetch_page=fetch_public_page, search=None):
        self.fetch_page = fetch_page
        self.search_provider = search
        self.sessions: OrderedDict[str, tuple[float, list[dict[str, str]]]] = OrderedDict()
        self._semaphore = asyncio.Semaphore(2)

    async def _call(self, fn, *args):
        async with self._semaphore:
            return await asyncio.wait_for(asyncio.to_thread(fn, *args), timeout=14)

    def _save_refs(self, session_id: str, refs: list[dict[str, str]]) -> None:
        now = asyncio.get_running_loop().time()
        for key, value in list(self.sessions.items()):
            if now - value[0] > 900:
                self.sessions.pop(key, None)
        self.sessions[session_id] = (now, refs[:12])
        self.sessions.move_to_end(session_id)
        while len(self.sessions) > 32:
            self.sessions.popitem(last=False)

    def _refs(self, session_id: str) -> list[dict[str, str]]:
        current = self.sessions.get(session_id)
        if current and asyncio.get_running_loop().time() - current[0] < 900:
            return current[1]
        self.sessions.pop(session_id, None)
        return []

    async def research(self, text: str, session_id: str | None) -> dict[str, Any] | None:
        intent = parse_web_intent(text)
        if intent is None:
            return None
        if not session_id:
            return {"action": intent.action, "status": "unavailable", "reason": "WEB_SESSION_REQUIRED",
                    "sources": [], "references": []}
        try:
            if intent.action == "follow":
                refs = self._refs(session_id)
                index = int(intent.argument) - 1
                if not 0 <= index < len(refs):
                    raise WebFetchError("WEB_REFERENCE_NOT_FOUND")
                return await self._open(refs[index]["url"], session_id, "follow")
            if intent.action == "open":
                return await self._open(intent.argument, session_id, "open")
            endpoint = os.getenv("AMORA_WEB_SEARCH_ENDPOINT", "").strip()
            providers = (
                [(self.search_provider, (intent.argument,))]
                if self.search_provider is not None else
                ([( _searx_search, (intent.argument, endpoint))] if endpoint else [])
                + [(_bing_rss_search, (intent.argument,)), (_ddg_search, (intent.argument,))]
            )
            results = []
            for provider, arguments in providers:
                try:
                    results = await self._call(provider, *arguments)
                    if results:
                        break
                except (WebFetchError, TimeoutError):
                    continue
            if not results:
                raise WebFetchError("WEB_SEARCH_UNAVAILABLE")
            validated: list[dict[str, str]] = []
            for item in results[:5]:
                try:
                    safe_url = _public_url(str(item.get("url") or ""))[0]
                except WebFetchError:
                    continue
                validated.append({"url": safe_url, "title": _clean(str(item.get("title") or ""), 160),
                                  "snippet": _clean(str(item.get("snippet") or ""), 300)})
            if not validated:
                raise WebFetchError("WEB_SEARCH_UNAVAILABLE")
            self._save_refs(session_id, validated)
            # Read two independent sources concurrently under the Core turn
            # timeout. Only actual opened pages are promoted to citations.
            fetched = await asyncio.gather(
                *(self._call(self.fetch_page, item["url"]) for item in validated[:2]),
                return_exceptions=True,
            )
            sources = [
                {key: page[key] for key in ("url", "title", "text", "fetched_at", "status")}
                for page in fetched if isinstance(page, dict) and page.get("status") == "opened"
            ]
            return {"action": "search", "status": "opened" if sources else "results_only",
                    "query": intent.argument[:240], "sources": sources,
                    "references": [{"number": index, "url": item["url"],
                                    "title": item["title"], "snippet": item.get("snippet", "")}
                                   for index, item in enumerate(validated, 1)]}
        except (WebFetchError, TimeoutError) as exc:
            code = exc.code if isinstance(exc, WebFetchError) else "WEB_TIMEOUT"
            return {"action": intent.action, "status": "failed", "reason": code,
                    "sources": [], "references": []}
        except Exception:
            # DNS/HTML/parser failures are not evidence. Avoid dumping user
            # URLs, fetched text or provider exceptions into production logs.
            return {"action": intent.action, "status": "failed", "reason": "WEB_RUNTIME_FAILED",
                    "sources": [], "references": []}

    async def _open(self, url: str, session_id: str, action: str) -> dict[str, Any]:
        page = await self._call(self.fetch_page, url)
        refs = page.get("links", []) if page.get("status") == "opened" else []
        self._save_refs(session_id, refs)
        source = {key: page.get(key, "") for key in (
            "url", "title", "text", "fetched_at", "status")}
        return {
            "action": action, "status": page.get("status", "failed"),
            "sources": [source] if page.get("status") == "opened" else [],
            "references": [{"number": index, "url": item["url"], "title": item["title"]}
                           for index, item in enumerate(refs[:12], 1)],
            **({"reason": "WEB_CONTENT_UNSUPPORTED"} if page.get("status") == "unsupported" else {}),
        }
