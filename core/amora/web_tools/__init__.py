"""Read-only web retrieval. Web content is untrusted evidence, never Core policy."""

from .navigator import WebNavigator, WebFetchError, fetch_public_page, parse_web_intent

__all__ = ["WebNavigator", "WebFetchError", "fetch_public_page", "parse_web_intent"]
