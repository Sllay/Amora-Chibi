"""Recognize explicit publication requests, never ordinary recall questions."""
from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

from amora.memory.recall_intent import normalize_memory_request, topic_terms
from .explicit_memory import _CREDENTIAL

_PREFIX = re.compile(
    r"^(?:(?:voce|vc|tu|ce)\s+)?(?:pode|podes)\s+"
    r"(?:falar|contar|compartilhar|mostrar|divulgar)\s+(?:aqui|neste canal|nesse canal|neste grupo|nesse grupo)\s+"
    r"|^(?:compartilhe|compartilha|divulgue|mostre|mostra|conte|conta)\s+"
    r"(?:aqui|neste canal|nesse canal|neste grupo|nesse grupo)\s+"
    r"|^autorizo\s+(?:(?:voce|vc|tu|ce)\s+a\s+)?(?:compartilhar|divulgar)\s+"
    r"(?:aqui|neste canal|nesse canal|neste grupo|nesse grupo)\s+"
)
_RECALL = re.compile(
    r"^(?:o\s*que\s+(?:(?:voce|vc|tu|ce)\s+)?(?:se\s+)?(?:lembra|recorda)\s+"
    r"(?:sobre|do|da|dos|das|de)\s+|(?:a\s+)?(?:memoria|lembranca)\s+(?:sobre|do|da|dos|das|de)\s+)"
)
_BROAD = re.compile(r"\b(?:tudo|todas|todos|qualquer|inteiro|inteira|completo|completa|historico|conversas)\b")
_ID = re.compile(r"^(?:a\s+)?memoria\s+([0-9a-f-]{36})$")
_SENTENCE = re.compile(r"(?<=[.!?])\s+|[\r\n]+")


@dataclass(frozen=True, slots=True)
class DisclosureIntent:
    topic: str = ""
    record_id: str | None = None
    valid: bool = False


def parse_disclosure_intent(text: str) -> DisclosureIntent | None:
    value = normalize_memory_request(text)
    if value is None:
        return None
    match = _PREFIX.match(value)
    if not match:
        return None
    subject = value[match.end():].strip()
    exact = _ID.fullmatch(subject)
    if exact:
        try:
            return DisclosureIntent(record_id=str(UUID(exact.group(1))), valid=True)
        except ValueError:
            return DisclosureIntent()
    # Permission to talk about a topic is not permission to disclose memory.
    # Require an explicit recollection/remembered-record reference.
    recall = _RECALL.match(subject)
    if recall is None:
        return DisclosureIntent() if _BROAD.search(subject) or re.search(r"\b(?:memoria|lembranca|lembrancas)\b", subject) else None
    subject = subject[recall.end():].strip()
    terms = topic_terms(subject)
    if not terms or len(subject) > 160 or len(terms) > 12 or _BROAD.search(subject):
        return DisclosureIntent()
    return DisclosureIntent(topic=subject, valid=True)


def disclosure_excerpt(record, intent: DisclosureIntent) -> str | None:
    """Publish a bounded excerpt, not the entire context or neighboring records.

    A topic request selects sentences that explicitly name the topic; it does
    not assume a following pronoun refers to the topic. An explicit record UUID
    instead authorizes that entire bounded record. Neither path summarizes with
    a remote model. Configured confidentiality guards still run at delivery.
    """
    content = record.content.strip()
    prefix = "Relato de PERSON_BK: "
    if content.startswith(prefix):
        content = content[len(prefix):].strip()
    if intent.record_id:
        result = content
    else:
        terms = topic_terms(intent.topic)
        sentences = [s.strip() for s in _SENTENCE.split(content) if s.strip()]
        chosen = [s for s in sentences if terms <= topic_terms(s)]
        if not chosen and record.summary:
            chosen = [s.strip() for s in _SENTENCE.split(record.summary)
                      if s.strip() and terms <= topic_terms(s)]
        result = " ".join(dict.fromkeys(chosen))
    if not result or len(result) > 1200 or _CREDENTIAL.search(result):
        return None
    return result
