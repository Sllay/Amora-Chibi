"""Grounded web sources and optional explicit screenshot intent for Discord.

This is a transport layer, not spoken realization: a model may say it has no
links, but never decides whether the Core-provided public URLs are clickable.
Only the verified owner's DM may call compose_web_delivery.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from amora.web_tools.navigator import WebFetchError, WebIntent, _public_url, screenshot_command

def screenshot_requested(text: str) -> bool:
    """Single explicit-capture detector shared with the navigation parser."""
    return screenshot_command(text)


_SCREENSHOT_DELIVERY_CLAIM = re.compile(
    r"\b(?:enviei|mandei|anexei|tirei|capturei|foi\s+enviad[oa]|"
    r"j[aá]\s+(?:enviei|mandei|anexei)|print\s+enviado|"
    r"segue\s+(?:o\s+)?(?:print|screenshot|anexo))\b",
    re.IGNORECASE,
)
_SCREENSHOT_TERMS = re.compile(
    r"\b(?:print|screenshot|captura|foto|imagem|anexo)\b", re.IGNORECASE
)


def neutralize_screenshot_claims(text: str) -> str:
    """Remove model-generated claims that only the Discord worker can verify.

    Retain substantive research results and URLs, changing only sentences
    that falsely assert screenshot generation/delivery.
    """
    pieces = re.split(r"(?<=[.!?\n])", str(text or ""))
    clean = []
    for piece in pieces:
        if (_SCREENSHOT_TERMS.search(piece) and
                _SCREENSHOT_DELIVERY_CLAIM.search(piece)):
            clean.append("A entrega do print depende do anexo no Discord. ")
        else:
            clean.append(piece)
    return "".join(clean).strip()


def _public_urls(values: Iterable[object], *, max_items: int) -> list[str]:
    urls: list[str] = []
    for value in values:
        raw = value.get("url") if isinstance(value, dict) else value
        if not isinstance(raw, str):
            continue
        try:
            safe = _public_url(raw)[0]
        except WebFetchError:
            continue
        if safe not in urls:
            urls.append(safe)
        if len(urls) >= max_items:
            break
    return urls


def compose_web_delivery(
    text: str,
    *,
    intent: WebIntent | None,
    sources: list[dict[str, Any]] | None = None,
    references: list[object] | None = None,
    session_references: list[object] | None = None,
    owner_verified: bool = False,
) -> tuple[str, str | None]:
    """Return clickable URLs and optional screenshot source.

    No action without verified transport owner + explicit web intent. Search
    listings have separate labels from pages actually opened. The optional
    screenshot uses only a valid public URL from this same turn/session.
    """
    content = str(text or "").strip()
    if not owner_verified or intent is None:
        return content, None
    # This request only asks for the previous page's image; old references
    # must not be presented as newly fetched web evidence.
    if intent.action in {"capture", "capture_retry"}:
        return content, None
    opened = _public_urls(sources or [], max_items=2)
    max_refs = 12 if intent.action in {"saved_links", "web_history"} else 5
    refs = _public_urls(references or session_references or [], max_items=max_refs)
    if intent.action == "search" and not refs and session_references:
        refs = _public_urls(session_references, max_items=5)

    # Search result links are useful even when language generation failed.
    # Never silently turn links-only into 'sources consulted'.
    paragraphs = [content] if content else []
    # OutboundMessage.text is capped at 12k; reserve headroom for formatting.
    # Long URLs must not turn a valid answer into a failed Discord outbound.
    available = max(0, 11700 - len(content))

    def append_lines(label: str, numbered: list[tuple[int, str]]) -> None:
        nonlocal available
        if not numbered or available <= len(label) + 6:
            return
        lines: list[str] = []
        used = len(label) + 2
        for index, url in numbered:
            row = f"{index}. {url}" if index > 0 else url
            if used + len(row) + 1 > available:
                break
            lines.append(row)
            used += len(row) + 1
        if lines:
            paragraphs.append(label + "\n" + "\n".join(lines))
            available -= used + 2

    append_lines("Fontes consultadas:", [(0, url) for url in opened])
    if refs:
        label = (
            "Links salvos na biblioteca da pesquisa:"
            if intent.action in {"saved_links", "pin_results"}
            else "Histórico de URLs (nem todas as páginas foram lidas):"
            if intent.action == "web_history"
            else "Resultado salvo:"
            if intent.action == "pin"
            else "Resultados da busca (nem todos lidos):"
            if intent.action == "search"
            else "Links encontrados nesta página (nem todos abertos):"
        )
        # Preserve the exact reference numbers exposed by WebNavigator:
        # omitting already opened refs here would shift 'abre o link 2'.
        append_lines(label, list(enumerate(refs, 1)))
    return "\n\n".join(paragraphs), None


def requested_screenshot_source(
    request_text: str,
    intent: WebIntent | None,
    *,
    sources: list[dict[str, Any]] | None = None,
    references: list[object] | None = None,
    session_references: list[object] | None = None,
    owner_verified: bool = False,
    authorized_retry_url: str | None = None,
) -> str | None:
    if not owner_verified or intent is None:
        return None
    retry = intent.action == "capture_retry"
    if retry:
        # Retry is allowed ONLY after WebNavigator validates its previous
        # verified-owner screenshot request lease in the same DM.
        if not authorized_retry_url:
            return None
        candidates = _public_urls([authorized_retry_url], max_items=1)
    else:
        if not screenshot_requested(request_text):
            return None
        candidates = _public_urls(sources or [], max_items=2)
    if not candidates and not retry and intent.action in {"search", "follow", "capture"}:
        candidates = _public_urls(
            references or session_references or [], max_items=1
        )
    if not candidates and not retry and intent.action == "open":
        candidates = _public_urls([intent.argument], max_items=1)
    if not candidates:
        return None
    # Renderer lives outside the Core. Do not share credential-like URLs.
    from amora.web_tools.remote_reader import _shareable_public_url
    try:
        return _shareable_public_url(candidates[0])
    except WebFetchError:
        return None
