"""Grounded web sources and optional explicit screenshot intent for Discord.

This is a transport layer, not spoken realization: a model may say it has no
links, but never decides whether the Core-provided public URLs are clickable.
Only the verified owner's DM may call compose_web_delivery.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from amora.web_tools.navigator import WebFetchError, WebIntent, _public_url

_SCREENSHOT_REQUEST = re.compile(
    r"\b(?:(?:tira|tirar|manda|mande|envia|enviar|mostra|mostre|faz|quero)"
    r".{0,42}?\b(?:print|screenshot|captura(?:\s+de\s+tela)?)\b"
    r"|(?:print|screenshot|captura)\s+(?:do|da|desse|dessa|de)"
    r"\s+(?:site|p[aá]gina|link|resultado|web))\b",
    re.IGNORECASE | re.DOTALL,
)


def screenshot_requested(text: str) -> bool:
    """User explicitly requested a page image, not a generic image topic."""
    return bool(_SCREENSHOT_REQUEST.search(str(text or "")[:1600]))


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
    opened = _public_urls(sources or [], max_items=2)
    refs = _public_urls(references or session_references or [], max_items=5)
    if intent.action == "search" and not refs and session_references:
        refs = _public_urls(session_references, max_items=5)

    # Search result links are useful even when language generation failed.
    # Never silently turn links-only into 'sources consulted'.
    paragraphs = [content] if content else []
    if opened:
        paragraphs.append("Fontes consultadas:\n" + "\n".join(opened))
    if refs:
        label = (
            "Resultados da busca (nem todos lidos):"
            if intent.action == "search"
            else "Links encontrados nesta página (nem todos abertos):"
        )
        # Preserve the exact reference numbers exposed by WebNavigator:
        # omitting already opened refs here would shift 'abre o link 2'.
        paragraphs.append(label + "\n" + "\n".join(
            f"{index}. {url}" for index, url in enumerate(refs, 1)
        ))
    return "\n\n".join(paragraphs), None


def requested_screenshot_source(
    request_text: str,
    intent: WebIntent | None,
    *,
    sources: list[dict[str, Any]] | None = None,
    references: list[object] | None = None,
    session_references: list[object] | None = None,
    owner_verified: bool = False,
) -> str | None:
    if not owner_verified or not screenshot_requested(request_text) or intent is None:
        return None
    candidates = _public_urls(sources or [], max_items=2)
    if not candidates and intent.action in {"search", "follow"}:
        candidates = _public_urls(
            references or session_references or [], max_items=1
        )
    if not candidates and intent.action == "open":
        candidates = _public_urls([intent.argument], max_items=1)
    if not candidates:
        return None
    # Renderer lives outside the Core. Do not share credential-like URLs.
    from amora.web_tools.remote_reader import _shareable_public_url
    try:
        return _shareable_public_url(candidates[0])
    except WebFetchError:
        return None
