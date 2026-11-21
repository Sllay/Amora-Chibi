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
import logging
import os
import re
import socket
import ssl
import unicodedata
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
_LOG = logging.getLogger("amora.web")

_LINK_RE = re.compile(r"https?://[^\s<>\"'\x60]+", re.IGNORECASE)
_SCREENSHOT_FOLLOW_RE = re.compile(
    r"\b(?:print|screenshot|captura|foto\s+da\s+p[aá]gina)\b"
    r".{0,40}?\b(?:link|resultado|refer[eê]ncia)\s*(?:n[ºo°.]?\s*)?(\d{1,2})\b",
    re.IGNORECASE | re.DOTALL,
)
_FOLLOW_RE = re.compile(
    r"\b(?:abre|abra|abrir|acessa|acesse|segue|seguir|leia|l[eê])\b"
    r".{0,35}?\b(?:link|resultado|refer[eê]ncia)\s*(?:n[ºo°.]?\s*)?(\d{1,2})\b",
    re.IGNORECASE | re.DOTALL,
)
_CAPTURE_RETRY_RE = re.compile(
    r"^\s*(?:amora[,\s]+)?(?:me\s+)?"
    r"(?:envie|envia|mande|manda|reenvi[ae])\s+"
    r"(?:(?:a|o|essa|esse)\s+)?(?:imagem|foto|arquivo|anexo)\s*[.!?]*$",
    re.IGNORECASE,
)
_CAPTURE_CURRENT_RE = re.compile(
    r"\b(?:tira|tirar|manda|mande|envia|enviar|mostra|mostre|faz|quero)\b"
    r".{0,48}?\b(?:print|screenshot|captura(?:\s+de\s+tela)?)\b",
    re.IGNORECASE | re.DOTALL,
)
_WEB_SAVED_RE = re.compile(
    r"\b(?:mostra|mostre|lista|liste|quais|ver|veja)\b"
    r".{0,50}?\b(?:links?\s+(?:salvos?|guardados?|favoritos?)|favoritos?)\b",
    re.IGNORECASE | re.DOTALL,
)
_WEB_HISTORY_RE = re.compile(
    r"\b(?:mostra|mostre|lista|liste|quais|ver|veja)\b"
    r".{0,50}?\b(?:hist[oó]rico\s+(?:de\s+)?(?:pesquisa|navega[cç][aã]o)|"
    r"sites?\s+(?:que\s+)?(?:abriu|acessou|visitou))\b",
    re.IGNORECASE | re.DOTALL,
)
_WEB_PIN_RE = re.compile(
    r"\b(?:salva|salve|guarda|guarde|favorita|favorite|marca|marque)\b"
    r".{0,35}?\b(?:link|resultado|refer[eê]ncia)\s*(?:n[ºo°.]?\s*)?(\d{1,2})\b",
    re.IGNORECASE | re.DOTALL,
)
_WEB_PIN_CURRENT_RE = re.compile(
    r"^\s*(?:amora[,\s]+)?(?:salva|salve|guarda|guarde|favorita|favorite)"
    r"\s+(?:(?:esse|este|essa|esta|o|a)\s+)?(?:link|site|p[aá]gina)\s*[.!?]*$",
    re.IGNORECASE,
)
_WEB_CONTINUE_RE = re.compile(
    r"\b(?:continua|continue|continuar|prossegue|prossiga)\b"
    r".{0,24}?\b(?:pesquisa|pesquisando|busca|buscando|investiga[cç][aã]o)\b",
    re.IGNORECASE | re.DOTALL,
)
_SEARCH_RE = re.compile(
    r"\b(?:pesquis[ae]|pesquise|buscar?|busque|procur[ae]|procure|"
    r"investig[ae]|investigue|busca\s+na\s+(?:web|internet)|"
    r"veja\s+na\s+(?:web|internet)|pesquisa\s+na\s+(?:web|internet))\b",
    re.IGNORECASE,
)
# Explicit anaphoric requests may reuse the last verified source's topic,
# never raw dialogue, ungrounded model guesses or unrelated memories.
_CONTEXT_SEARCH_RE = re.compile(
    r"\b(?:de\s+novo|novamente|outra\s+vez|mais\s+detalhes|"
    r"mesmo\s+(?:assunto|site)|sobre\s+(?:isso|isto)|"
    r"dessa\s+p[aá]gina|desse\s+site)\b",
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
            if (not literal_ip.is_global or literal_ip.is_multicast or literal_ip.is_reserved or literal_ip.is_unspecified):
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
            if (not parsed.is_global or parsed.is_multicast or parsed.is_reserved or parsed.is_unspecified):
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


# The search index receives topical keywords, not a raw Discord sentence.
# The stopped words are grammatical/interaction fillers, never a list of
# approved subjects or domains. Negations and technical terms are retained.
_SEARCH_FILLERS = frozenset({
    "a", "o", "as", "os", "um", "uma", "uns", "umas", "de", "da", "do", "das",
    "dos", "no", "na", "nos", "nas", "em", "e", "que", "qual", "quais",
    "sobre", "por", "favor", "pra", "para", "pelo", "pela", "pelos", "pelas",
    "me", "diz", "diga", "fala", "fale", "mostra", "mostre", "envia",
    "mande", "encontre", "agora", "entao", "então", "ai", "aí", "ae",
})
_SEARCH_GENERIC = frozenset({
    "novidade", "novidades", "noticia", "noticias", "news",
    "recentes", "recente", "atual", "atualizacoes", "atualizacao",
    "ultimas", "ultimos", "ultima", "ultimo", "informacoes", "informacao",
})


def _clean_search_query(raw: str, match: re.Match[str]) -> str:
    """Extract the subject after the search verb; remove casual preambles.

    Example: 'Okay então Amora pesquisa novidade sobre Android na web da
    Godot Engine' -> 'novidade Android Godot Engine'. No model call needed.
    """
    subject = raw[match.end():match.end() + 400]
    # 'na web' describes the medium; preserve 'web' when it belongs to
    # a technical topic such as 'pesquise WebAssembly'.
    subject = re.sub(r"(?i)\b(?:na|pela|pela?\s+a|pela\s+|pela|por|via)\s+(?:web|internet)\b", " ", subject)
    subject = re.split(
        r"(?i)\s+e\s+(?:(?:me|mim)\s+)?"
        r"(?:diz|diga|fala|fale|mostra|mostre|manda|mande|mandar|"
        r"envia|envie|enviar|explica|explique|descreve|descreva|faz|fa[cç]a)\b",
        subject, maxsplit=1,
    )[0]
    # "pesquisa de novo e me manda uma descrição" is a contextual request,
    # not a search for "novo descrição". Preserve explicitly named topics.
    subject = re.sub(
        r"(?i)^\s*(?:(?:de\s+novo|novamente|outra\s+vez)\b[\s,;:]*)+",
        "", subject,
    )
    subject = re.sub(
        r"(?i)^\s*(?:(?:mais\s+)?detalhes(?:\s+sobre\s+(?:isso|isto|"
        r"esse\s+site|essa\s+p[aá]gina))?|"
        r"(?:mais\s+)?(?:sobre\s+)?(?:isso|isto|esse\s+site|"
        r"essa\s+p[aá]gina|o\s+mesmo\s+assunto))"
        r"(?:\s+de\s+novo)?\s*$",
        "", subject,
    )
    # Screenshot delivery is a separate transport instruction, never part of
    # the semantic search query (e.g. "Godot Android manda print").
    subject = re.split(
        r"(?i)\s+(?:e\s+)?(?:me\s+)?(?:tira|tirar|manda|mande|envia|enviar)"
        r"\s+(?:um\s+|uma\s+|o\s+|a\s+)?(?:print|screenshot|captura)\b",
        subject, maxsplit=1,
    )[0]
    words = re.findall(r'"[^"\n]+"|[\w][\w.+#-]*', subject, flags=re.UNICODE)
    terms = [word for word in words if word.casefold() not in _SEARCH_FILLERS]
    return " ".join(terms[:28])[:240].strip()


def _fold_search(value: str) -> str:
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", value.casefold())
        if not unicodedata.combining(ch)
    )


def _page_topic(source: dict[str, Any]) -> str:
    """Derive a small topic from a *successfully opened* public page.

    Titles are untrusted data: use only a short title segment matching the
    fetched URL host, otherwise fall back to the host identifier. No page
    instructions, HTML, transcript or long text become query context.
    """
    url = str(source.get("url") or "")
    try:
        host = (urlsplit(url).hostname or "").lower().strip(".")
    except ValueError:
        return ""
    if not host:
        return ""
    parts = [part for part in host.split(".") if part not in {
        "www", "m", "en", "docs", "forum", "wiki", "com", "org",
        "net", "io", "co", "br", "gov", "edu",
    }]
    entity = parts[-1] if parts else ""
    if entity in {"github", "gitlab", "reddit", "wikipedia"}:
        slug = [part for part in urlsplit(url).path.split("/")
                if re.fullmatch(r"[a-zA-Z][a-zA-Z0-9_-]{3,48}", part)]
        if slug:
            entity = slug[0]
    if not re.fullmatch(r"[a-z0-9-]{4,55}", entity):
        return ""
    title = _clean(str(source.get("title") or ""), 150)
    for segment in re.split(r"\s+[|–—-]\s+", title):
        candidate = re.sub(r"[^\w]+", "", _fold_search(segment))
        if (len(segment) <= 65 and len(segment.split()) <= 3
                and entity.replace("-", "") in candidate
                and not re.search(r"(?i)\b(?:ignore|instruction|prompt|system|senha|secret)\b", segment)):
            return segment.strip()
    return entity.replace("-", " ")


def _topic_terms(query: str) -> list[str]:
    seen: set[str] = set()
    terms = []
    for token in re.findall(r"\w+", _fold_search(query)):
        if len(token) < 2 or token in _SEARCH_GENERIC or token in _SEARCH_FILLERS or token in seen:
            continue
        seen.add(token)
        terms.append(token)
    return terms[:20]


def _rank_search_results(results: list[dict[str, str]], query: str) -> list[dict[str, str]]:
    """Discard off-topic search listings instead of letting them become cited.

    Search providers occasionally return a real but unrelated URL. No public
    domain is privileged or prohibited here: ranking uses the *user topic*.
    """
    topics = _topic_terms(query)
    minimum_matches = min(2, len(topics))
    scored = []
    seen_urls: set[str] = set()
    for index, item in enumerate(results[:15]):
        if not isinstance(item, dict):
            continue
        try:
            url = _public_url(str(item.get("url") or ""))[0]
        except WebFetchError:
            continue
        if url in seen_urls:
            continue
        title = _clean(str(item.get("title") or ""), 160)
        snippet = _clean(str(item.get("snippet") or ""), 300)
        title_norm = _fold_search(title)
        snippet_norm = _fold_search(snippet)
        # URL host/path can legitimately support matching the requested
        # product/project even if the search engine returns a terse title.
        url_norm = _fold_search(urlsplit(url).hostname or "") + " " + _fold_search(urlsplit(url).path)
        matches = sum(1 for term in topics if term in title_norm or term in snippet_norm or term in url_norm)
        if topics and matches < minimum_matches:
            continue
        score = (
            matches * 4
            + sum(5 for term in topics if term in title_norm)
            + sum(2 for term in topics if term in url_norm)
            + sum(1 for term in topics if term in snippet_norm)
        )
        seen_urls.add(url)
        scored.append((-score, index, {"url": url, "title": title, "snippet": snippet}))
    scored.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in scored[:5]]


def parse_web_intent(text: str) -> WebIntent | None:
    raw = str(text or "")[:12000]
    urls = _LINK_RE.findall(raw)
    if len(urls) > 1:
        # Batch only small explicit link lists. Each URL still runs through the
        # same public-network fence, never arbitrary user-supplied commands.
        return WebIntent("open_many", json.dumps([
            url.rstrip(".,;:!?\u00bb") for url in urls[:3]
        ]))
    if urls:
        first = urls[0].rstrip(".,;:!?\u00bb")
        # Markdown/chat often add an unmatched closing parenthesis after a
        # URL. Keep legitimate trailing ')' in Wikipedia/article links.
        while first.endswith(")") and first.count(")") > first.count("("):
            first = first[:-1]
        return WebIntent("open", first)
    pin = _WEB_PIN_RE.search(raw)
    if pin:
        return WebIntent("pin", pin.group(1))
    if _WEB_PIN_CURRENT_RE.search(raw):
        return WebIntent("pin", "current")
    if _WEB_SAVED_RE.search(raw):
        return WebIntent("saved_links", "")
    if _WEB_HISTORY_RE.search(raw):
        return WebIntent("web_history", "")
    if _WEB_CONTINUE_RE.search(raw):
        return WebIntent("search", "")
    follow = _FOLLOW_RE.search(raw) or _SCREENSHOT_FOLLOW_RE.search(raw)
    if follow:
        return WebIntent("follow", follow.group(1))
    search = _SEARCH_RE.search(raw)
    if search:
        # Do not send greetings, 'Okay', user instructions or Amora's name
        # ahead of the actual subject to search engines.
        return WebIntent("search", _clean_search_query(raw, search))
    if _CAPTURE_CURRENT_RE.search(raw):
        return WebIntent("capture", "current")
    # A bare "envie a imagem" may refer to the preceding screenshot request;
    # the navigator still requires an owner-DM capture lease before acting.
    if _CAPTURE_RETRY_RE.search(raw):
        return WebIntent("capture_retry", "previous")
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
            try:
                link_url = _public_url(item["url"])[0]
            except WebFetchError:
                continue
            normalized_link = {"url": link_url, "title": item["title"]}
            if link_url != final_url and normalized_link not in links:
                links.append(normalized_link)
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
        if len(results) >= 12:
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
        if len(results) >= 12:
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
        if len(results) >= 12:
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

    def __init__(self, *, fetch_page=fetch_public_page, search=None, browser=None,
                 reader=None, planner=None, workspace=None):
        self.fetch_page = fetch_page
        self.search_provider = search
        # Planner is injected only after Core verifies owner-DM authorization.
        # No extra network service is enabled by a standalone navigator.
        self.planner = planner
        self.workspace = workspace
        self._hydrated_sessions: set[str] = set()
        # Test-injected fetch/search providers cannot accidentally send URLs
        # to external vendors. The production Core uses the defaults.
        if browser is None and fetch_page is fetch_public_page and search is None:
            from .browser_client import BrowserClient
            browser = BrowserClient.from_environment()
        if reader is None and fetch_page is fetch_public_page and search is None:
            from .remote_reader import RemoteReader
            reader = RemoteReader.from_environment()
        self.browser = browser
        self.reader = reader
        self.sessions: OrderedDict[str, tuple[float, list[dict[str, str]]]] = OrderedDict()
        self.last_delivery: OrderedDict[str, tuple[float, dict[str, Any]]] = OrderedDict()
        # URL-only, per-DM short-lived lease for explicit screenshot retry.
        # Not a memory record; no page text or conversation is retained.
        self.last_capture_requests: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._semaphore = asyncio.Semaphore(2)

    @staticmethod
    def _needs_browser(page: dict[str, Any]) -> bool:
        if page.get("status") != "opened":
            return True
        text = str(page.get("text") or "").casefold()
        return len(text.strip()) < 24 or any(cue in text[:500] for cue in (
            "enable javascript", "javascript is required",
            "ativar o javascript", "habilite javascript",
            "loading...", "just a moment", "checking your browser",
        ))

    async def _read_page(self, url: str, *, force_browser: bool = False) -> dict[str, Any]:
        _public_url(url)  # Private literals fail before invoking any vendor.
        page = None
        original_error = None
        if not force_browser or (self.reader is None and self.browser is None):
            try:
                page = await self._call(self.fetch_page, url)
            except (WebFetchError, TimeoutError) as exc:
                original_error = exc
        if force_browser or page is None or self._needs_browser(page):
            rendered = await self._remote_render(url)
            if rendered is not None:
                return rendered
        if page is not None:
            # A JavaScript shell / anti-bot interstitial is not article text.
            # If remote rendering failed, never claim it was read successfully.
            if self._needs_browser(page):
                raise WebFetchError("WEB_PAGE_CONTENT_UNAVAILABLE")
            return page
        if original_error is not None:
            raise original_error
        raise WebFetchError("WEB_REMOTE_RENDER_UNAVAILABLE")

    async def _remote_render(self, url: str) -> dict[str, Any] | None:
        # Hosted JS first; an explicitly configured Chromium worker is last.
        for provider in (self.reader, self.browser):
            if provider is None:
                continue
            try:
                page = await asyncio.wait_for(provider.render(url), timeout=10.0)
                if isinstance(page, dict) and page.get("status") == "opened":
                    return page
            except (WebFetchError, TimeoutError):
                continue
        return None

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

    def current_public_references(self, session_id: str) -> list[dict[str, str]]:
        """Snapshot Core-held URLs for transport; never model invented links."""
        return [dict(item) for item in self._refs(session_id)[:5]]

    def _record_delivery(self, session_id: str, result: dict[str, Any]) -> dict[str, Any]:
        # Only source/ref URL metadata survives briefly for the Discord worker
        # after provider fallback. Never retain fetched HTML or user text here.
        if result.get("status") in {"opened", "results_only"}:
            sources = result.get("sources") or []
            topic = str(result.get("topic") or "").strip()
            if not topic and sources and isinstance(sources[0], dict):
                topic = _page_topic(sources[0])
            projection = {
                "action": result.get("action", ""),
                "focus": _clean(topic, 90),
                "sources": [
                    {"url": str(item.get("url") or ""), "title": str(item.get("title") or "")}
                    for item in (result.get("sources") or [])[:2] if isinstance(item, dict)
                    and item.get("status") == "opened"
                ],
                "references": [
                    {"url": str(item.get("url") or ""), "title": str(item.get("title") or "")}
                    for item in (result.get("references") or [])[:12] if isinstance(item, dict)
                ],
            }
            self.last_delivery[session_id] = (asyncio.get_running_loop().time(), projection)
            if self.workspace is not None:
                try:
                    self.workspace.record_visit(session_id, result)
                except Exception:
                    _LOG.warning("WEB_WORKSPACE_PERSIST_FAILED")
            self.last_delivery.move_to_end(session_id)
            while len(self.last_delivery) > 32:
                self.last_delivery.popitem(last=False)
        return result

    def current_delivery(self, session_id: str) -> dict[str, Any]:
        record = self.last_delivery.get(session_id)
        if record and asyncio.get_running_loop().time() - record[0] < 900:
            data = record[1]
            return {
                "action": data["action"],
                "focus": data.get("focus", ""),
                "sources": [dict(item) for item in data["sources"]],
                "references": [dict(item) for item in data["references"]],
            }
        self.last_delivery.pop(session_id, None)
        return {"action": "", "focus": "", "sources": [], "references": []}

    def remember_capture_request(self, session_id: str, public_url: str) -> None:
        """Mark a real, verified owner request so an immediate retry is possible."""
        from .remote_reader import _shareable_public_url
        safe = _shareable_public_url(public_url)
        now = asyncio.get_running_loop().time()
        self.last_capture_requests[session_id] = (now, safe)
        self.last_capture_requests.move_to_end(session_id)
        while len(self.last_capture_requests) > 32:
            self.last_capture_requests.popitem(last=False)

    def current_capture_target(self, session_id: str) -> str | None:
        record = self.last_capture_requests.get(session_id)
        if record and asyncio.get_running_loop().time() - record[0] < 600:
            return record[1]
        self.last_capture_requests.pop(session_id, None)
        return None

    def _hydrate_workspace(self, session_id: str) -> None:
        """Restore references after process restart without re-fetching URLs."""
        if self.workspace is None or session_id in self._hydrated_sessions:
            return
        self._hydrated_sessions.add(session_id)
        try:
            rows = self.workspace.recent(session_id, limit=1)
        except Exception:
            _LOG.warning("WEB_WORKSPACE_RESTORE_FAILED")
            return
        if not rows:
            return
        record = rows[0]
        entries = record.get("entries") or []
        sources = [e for e in entries if isinstance(e, dict)
                   and e.get("state") == "read" and e.get("url")]
        refs = [e for e in (record.get("references") or [])
                if isinstance(e, dict) and e.get("url")]
        projection = {
            "action": str(record.get("action") or ""),
            "focus": str(record.get("topic") or "")[:90]
                or (_page_topic(sources[0]) if sources else ""),
            "sources": [{"url": e["url"], "title": str(e.get("title") or "")}
                        for e in sources[:2]],
            "references": [{"url": e["url"], "title": str(e.get("title") or "")}
                           for e in refs[:12]],
        }
        self.last_delivery[session_id] = (asyncio.get_running_loop().time(), projection)
        self._save_refs(session_id, projection["references"])

    def _workspace_notebook(self, session_id: str) -> dict[str, Any]:
        if self.workspace is None:
            return {}
        try:
            return self.workspace.notebook(session_id)
        except Exception:
            _LOG.warning("WEB_WORKSPACE_READ_FAILED")
            return {}

    async def research(self, text: str, session_id: str | None) -> dict[str, Any] | None:
        intent = parse_web_intent(text)
        if intent is None:
            return None
        if session_id:
            self._hydrate_workspace(session_id)
        result = await self._research_intent(text, session_id, intent)
        if result is not None and session_id:
            notebook = self._workspace_notebook(session_id)
            if notebook:
                result["research_notebook"] = notebook
        return result

    async def _research_intent(self, text: str, session_id: str | None,
                               intent: WebIntent) -> dict[str, Any] | None:
        if not session_id:
            return {"action": intent.action, "status": "unavailable", "reason": "WEB_SESSION_REQUIRED",
                    "sources": [], "references": []}
        if intent.action in {"saved_links", "web_history", "pin"}:
            if self.workspace is None:
                return {"action": intent.action, "status": "failed",
                        "reason": "WEB_WORKSPACE_UNAVAILABLE",
                        "sources": [], "references": []}
            try:
                if intent.action == "saved_links":
                    pins = self.workspace.bookmarks(session_id, 20)
                    refs = [{"url": p["url"], "title": p.get("title", ""),
                             "state": "saved"} for p in pins if p.get("url")]
                    return self._record_delivery(session_id, {
                        "action": "saved_links",
                        "status": "results_only" if refs else "empty",
                        "sources": [], "references": refs[:12],
                    })
                if intent.action == "web_history":
                    visits = self.workspace.recent(session_id, 24)
                    found, seen = [], set()
                    for record in visits:
                        for item in record.get("entries", []):
                            if item.get("url") and item["url"] not in seen:
                                seen.add(item["url"])
                                found.append(item)
                    return self._record_delivery(session_id, {
                        "action": "web_history",
                        "status": "results_only" if found else "empty",
                        "sources": [], "references": found[:12],
                    })
                refs = self._refs(session_id)
                previous = self.current_delivery(session_id)
                if intent.argument == "current":
                    options = previous["sources"] or previous["references"]
                    target = options[0] if options else None
                else:
                    idx = int(intent.argument) - 1
                    target = refs[idx] if 0 <= idx < len(refs) else None
                if not target:
                    raise WebFetchError("WEB_REFERENCE_NOT_FOUND")
                pinned = self.workspace.pin(session_id, target)
                existing = any(entry.get("url") == target.get("url")
                               for entry in self.workspace.bookmarks(session_id))
                if not pinned and not existing:
                    raise WebFetchError("WEB_BOOKMARK_PERSIST_FAILED")
                return {"action": "pin", "status": "saved",
                        "sources": [], "references": [target]}
            except WebFetchError as exc:
                return {"action": intent.action, "status": "failed",
                        "reason": exc.code, "sources": [], "references": []}
            except Exception:
                _LOG.warning("WEB_WORKSPACE_ACTION_FAILED")
                return {"action": intent.action, "status": "failed",
                        "reason": "WEB_WORKSPACE_ACTION_FAILED",
                        "sources": [], "references": []}
        if intent.action == "capture_retry":
            # Do not guess which image the user means. Only a recent verified
            # screenshot URL from this exact private Discord session qualifies.
            if self.current_capture_target(session_id) is not None:
                return {"action": "capture_retry", "status": "capture_pending",
                        "sources": [], "references": []}
            return {"action": "capture_retry", "status": "failed",
                    "reason": "WEB_SCREENSHOT_NO_PENDING_REQUEST",
                    "sources": [], "references": []}
        if intent.action == "capture":
            # A screenshot-only follow-up must use the last actual page/result
            # from the same verified DM. No old link is claimed as freshly read,
            # and this does not launch a new web request.
            previous = self.current_delivery(session_id)
            if previous["sources"] or previous["references"]:
                return {"action": "capture", "status": "capture_pending",
                        "sources": [], "references": []}
            return {"action": "capture", "status": "failed",
                    "reason": "WEB_SCREENSHOT_NO_PAGE",
                    "sources": [], "references": []}
        # Resolve anaphora BEFORE invalidating previous delivery evidence.
        # An explicit new topic always wins; an empty generic command alone
        # cannot silently search some random earlier subject.
        search_topic = intent.argument
        contextual_search = (
            intent.action == "search" and not search_topic
            and (_CONTEXT_SEARCH_RE.search(text) is not None
                 or _WEB_CONTINUE_RE.search(text) is not None)
        )
        if contextual_search:
            search_topic = self.current_delivery(session_id).get("focus", "")
            # Safe diagnostic event only: never log the subject, URL,
            # Discord conversation, user content or session identifier.
            _LOG.info(
                "WEB_SEARCH_CONTEXT_RESOLVED" if search_topic
                else "WEB_SEARCH_CONTEXT_MISSING"
            )
        # Navigating to a new URL/search invalidates prior attachment retries.
        self.last_capture_requests.pop(session_id, None)
        # A failed NEW navigation must never use a previous turn's links.
        self.last_delivery.pop(session_id, None)
        try:
            if intent.action == "follow":
                refs = self._refs(session_id)
                index = int(intent.argument) - 1
                if not 0 <= index < len(refs):
                    raise WebFetchError("WEB_REFERENCE_NOT_FOUND")
                return await self._open(refs[index]["url"], session_id, "follow",
                                        force_browser="navegador" in text.casefold())
            if intent.action == "open_many":
                try:
                    urls = json.loads(intent.argument)
                except (TypeError, ValueError):
                    raise WebFetchError("WEB_URL_INVALID") from None
                if not isinstance(urls, list) or not 1 <= len(urls) <= 3:
                    raise WebFetchError("WEB_URL_INVALID")
                for url in urls:
                    _public_url(url)
                pages = await asyncio.gather(
                    *(self._read_page(url) for url in urls),
                    return_exceptions=True,
                )
                opened = []
                for page in pages:
                    if (isinstance(page, dict)
                            and page.get("status") == "opened"
                            and not self._needs_browser(page)):
                        opened.append({
                            key: page.get(key, "") for key in
                            ("url", "title", "text", "fetched_at", "status")
                        })
                refs = [{"url": url, "title": urlsplit(url).hostname or ""}
                        for url in urls]
                self._save_refs(session_id, refs)
                return self._record_delivery(session_id, {
                    "action": "open_many",
                    "status": "opened" if opened else "results_only",
                    "sources": opened,
                    "references": refs,
                })
            if intent.action == "open":
                return await self._open(intent.argument, session_id, "open",
                                        force_browser="navegador" in text.casefold()
                                        or "chromium" in text.casefold())
            # No old links may masquerade as evidence of a new search.
            self._save_refs(session_id, [])
            if not search_topic:
                raise WebFetchError(
                    "WEB_SEARCH_CONTEXT_REQUIRED" if contextual_search
                    else "WEB_SEARCH_QUERY_EMPTY"
                )
            planned_queries = [search_topic]
            if self.planner is not None:
                try:
                    planned = await asyncio.wait_for(
                        self.planner.plan(text[:1200], search_topic), timeout=3.8
                    )
                    if isinstance(planned, list) and planned:
                        # A caller cannot inject unsafe search text through
                        # planner output. Production Groq has its own validation.
                        from .query_planner import validated_queries
                        checked = validated_queries({"queries": planned}, search_topic)
                        if checked:
                            planned_queries = checked
                except (TimeoutError, WebFetchError, ValueError, TypeError):
                    pass
            endpoint = os.getenv("AMORA_WEB_SEARCH_ENDPOINT", "").strip()
            validated: list[dict[str, str]] = []
            got_results = False
            used_query = search_topic
            for search_query in planned_queries[:2]:
                providers = (
                    [(self.search_provider, (search_query,))]
                    if self.search_provider is not None else
                    ([( _searx_search, (search_query, endpoint))] if endpoint else [])
                    + [(_bing_rss_search, (search_query,)), (_ddg_search, (search_query,))]
                )
                for provider, arguments in providers:
                    try:
                        results = await self._call(provider, *arguments)
                    except (WebFetchError, TimeoutError):
                        continue
                    if not results:
                        continue
                    got_results = True
                    # Rank AGAINST the original request, not merely the
                    # model-generated wording, to stop semantic topic drift.
                    validated = _rank_search_results(results, search_topic)
                    if validated:
                        used_query = search_query
                        break
                    # Search provider may reply successfully with nonsense.
                if validated:
                    break
            if not validated:
                raise WebFetchError(
                    "WEB_SEARCH_NO_RELEVANT_RESULTS" if got_results
                    else "WEB_SEARCH_UNAVAILABLE"
                )
            self._save_refs(session_id, validated)
            # Read two independent sources concurrently under the Core turn
            # timeout. Only actual opened pages are promoted to citations.
            fetched = await asyncio.gather(
                *(self._call(self.fetch_page, item["url"]) for item in validated[:2]),
                return_exceptions=True,
            )
            sources = [
                {
                    **{key: page[key] for key in ("url", "title", "text", "fetched_at", "status")},
                    **({"retrieval": page["retrieval"]} if page.get("retrieval") else {}),
                }
                for page in fetched if isinstance(page, dict) and page.get("status") == "opened"
                and not self._needs_browser(page)
            ]
            # If none of the two HTTP reads contains usable content, one
            # hosted JS read is sufficient. This does not launch local Chrome.
            if not sources and (self.reader is not None or self.browser is not None):
                rendered = await self._remote_render(validated[0]["url"])
                if rendered is not None:
                    sources.append({
                        **{key: rendered[key] for key in (
                            "url", "title", "text", "fetched_at", "status"
                        )},
                        **({"retrieval": rendered["retrieval"]} if rendered.get("retrieval") else {}),
                    })
            return self._record_delivery(session_id, {
                "action": "search", "status": "opened" if sources else "results_only",
                "query": used_query[:240], "topic": search_topic[:90], "sources": sources,
                "references": [{"number": index, "url": item["url"],
                                "title": item["title"], "snippet": item.get("snippet", "")}
                               for index, item in enumerate(validated, 1)],
            })
        except (WebFetchError, TimeoutError) as exc:
            code = exc.code if isinstance(exc, WebFetchError) else "WEB_TIMEOUT"
            return {"action": intent.action, "status": "failed", "reason": code,
                    "sources": [], "references": []}
        except Exception:
            # DNS/HTML/parser failures are not evidence. Avoid dumping user
            # URLs, fetched text or provider exceptions into production logs.
            return {"action": intent.action, "status": "failed", "reason": "WEB_RUNTIME_FAILED",
                    "sources": [], "references": []}

    async def _open(self, url: str, session_id: str, action: str,
                    *, force_browser: bool = False) -> dict[str, Any]:
        page = await self._read_page(url, force_browser=force_browser)
        refs = page.get("links", []) if page.get("status") == "opened" else []
        # A result page with zero extracted links must not erase the previous
        # numbered search list. The user may still ask "abre o link 3" after
        # reading result 2. A new explicit URL resets the list normally.
        if refs or action != "follow":
            self._save_refs(session_id, refs)
        source = {key: page.get(key, "") for key in (
            "url", "title", "text", "fetched_at", "status")}
        if page.get("retrieval"):
            source["retrieval"] = page["retrieval"]
        return self._record_delivery(session_id, {
            "action": action, "status": page.get("status", "failed"),
            "sources": [source] if page.get("status") == "opened" else [],
            "references": [{"number": index, "url": item["url"], "title": item["title"]}
                           for index, item in enumerate(refs[:12], 1)],
            **({"reason": "WEB_CONTENT_UNSUPPORTED"} if page.get("status") == "unsupported" else {}),
        })
