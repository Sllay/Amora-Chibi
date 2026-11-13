"""Minimal publication defense for explicitly configured confidential literals.

This is not a semantic confidentiality classifier. Configuration must be supplied
through the deployment environment, never committed with private values.
"""
import json
import os
import unicodedata


def _normalize(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def public_text_allowed(text: str) -> bool:
    raw = os.environ.get("AMORA_CONFIDENTIAL_LITERALS_JSON", "[]")
    try:
        values = json.loads(raw)
    except (ValueError, TypeError):
        return False
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not _normalize(value) for value in values
    ):
        return False
    normalized = _normalize(text)
    return not any(_normalize(value) in normalized for value in values)
