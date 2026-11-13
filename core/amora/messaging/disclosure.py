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


def audience_text_allowed(text: str, *, audience_id: str | None = None) -> bool:
    """Check explicitly registered confidential facts against destination audience.

    Registry entries contain an opaque ID, literal aliases and approved audience
    IDs. An empty audience list means no shared destination is authorized.
    The caller must supply a trusted transport-derived audience, not a speaker ID.
    """
    try:
        entries = json.loads(os.environ.get("AMORA_DISCLOSURE_REGISTRY_JSON", "[]"))
    except (ValueError, TypeError):
        return False
    if not isinstance(entries, list):
        return False
    normalized = _normalize(text)
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            return False
        fact_id = entry.get("id")
        aliases = entry.get("aliases")
        audiences = entry.get("audiences")
        if (not isinstance(fact_id, str) or not fact_id.strip() or fact_id in seen
                or not isinstance(aliases, list) or not aliases
                or any(not isinstance(a, str) or not _normalize(a) for a in aliases)
                or not isinstance(audiences, list)
                or any(not isinstance(a, str) or not a.strip() for a in audiences)):
            return False
        seen.add(fact_id)
        if any(_normalize(alias) in normalized for alias in aliases):
            if audience_id is None or audience_id not in audiences:
                return False
    return True
