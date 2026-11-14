"""Narrow, local intent for an owner-requested memory.

A conversational model cannot grant this permission. Only a stable verified
Discord identity, a private DM and an explicit bounded request can do so.
The source is stored as an attributed user report, never a verified world fact.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

OWNER_ENTITY_ID = "PERSON_BK"
PROVENANCE_SOURCE = "cognition:explicit_owner_request"
_MAX_CONTENT = 2000

# Match commands, not the substring "lembra" in a normal recollection question.
_VERB = r"(?:lembr[ea](?:-se)?|guard[ae]|salv[ae]|anot[ae]|registr[ae]|memorize)"
_WANT = r"(?:quero\s+que\s+(?:voc[eê]|vc|tu|c[eê])\s+)?"
_TARGET = r"(?:disso|disto|isso|isto|essa\s+informa[cç][aã]o)"
_REQUEST = rf"(?:{_WANT}{_VERB}\s+{_TARGET}(?:\s+na\s+mem[oó]ria)?|n[aã]o\s+esque[cç][ae]\s+{_TARGET})"
_SUFFIX = re.compile(rf"(?:^|[.!;]\s+|\n+){_REQUEST}[.!]?\s*$", re.I)
_PREFIX = re.compile(
    rf"^(?:{_WANT}{_VERB}\s+(?:na\s+mem[oó]ria\s*(?:[:,-]\s*|que\s+)|(?:de\s+)?que\s+)|"
    rf"{_WANT}{_VERB}\s+{_TARGET}\s+na\s+mem[oó]ria\s*:\s*)",
    re.I,
)
_VOCATIVE = re.compile(r"^amora\s*[,!:]\s*", re.I)
# A deliberately conservative credential guard, not a general DLP classifier.
_CREDENTIAL = re.compile(
    r"\b(?:senha|password|api[_ -]?key|chave\s+(?:de\s+api|privada)|"
    r"token\s+(?:de\s+acesso|de\s+autentica[cç][aã]o)|c[oó]digo\s+de\s+verifica[cç][aã]o)"
    r"\s*(?:[eé]\b|[:=])|-----BEGIN [A-Z ]*PRIVATE KEY-----",
    re.I,
)


@dataclass(frozen=True)
class ExplicitMemoryIntent:
    content: str | None
    reason: str


def parse_explicit_memory(text: str) -> ExplicitMemoryIntent | None:
    value = _VOCATIVE.sub("", text.strip(), count=1)
    # Questions, quoted commands, and reported speech are not explicit requests.
    if not value or value.endswith("?") or value.startswith(("\"", "'", "“", "«", ">")):
        return None
    prefix = _PREFIX.match(value)
    suffix = _SUFFIX.search(value)
    if prefix:
        content = value[prefix.end():].strip()
        if suffix and suffix.start() >= prefix.end():
            content = value[prefix.end():suffix.start()].strip()
    elif suffix:
        content = value[:suffix.start()].strip()
    else:
        return None
    if not content or re.fullmatch(r"(?:(?:isso|isto|disso|disto|aquilo|tudo)[.!]?)", content, re.I):
        return ExplicitMemoryIntent(None, "EXPLICIT_MEMORY_NEEDS_CONTENT")
    if len(content) > _MAX_CONTENT:
        return ExplicitMemoryIntent(None, "EXPLICIT_MEMORY_CONTENT_TOO_LONG")
    if _CREDENTIAL.search(content):
        return ExplicitMemoryIntent(None, "EXPLICIT_MEMORY_CREDENTIAL_BLOCKED")
    return ExplicitMemoryIntent(content, "EXPLICIT_MEMORY_REQUESTED")


def is_verified_owner(profiles, message, speaker_entity_id: str | None) -> bool:
    # Never authorize by alias, display name, relationship claim or an observed
    # identity binding. Callers independently enforce the allowed channel scope.
    if speaker_entity_id != OWNER_ENTITY_ID or message.platform.value != "discord":
        return False
    return any(
        profiles.has_verified_discord_binding(
            entity_id=OWNER_ENTITY_ID, identifier_type=ref.identifier_type,
            external_actor_id=ref.external_actor_id, account_scope=message.account_scope,
        )
        for ref in message.sender_identifiers
    )
