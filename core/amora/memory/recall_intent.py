"""Conservative Portuguese requests for persistent memory, not normal dialogue.

A named recall must match the subject rather than an unrelated entity-linked
memory. Group requests never authorize a private projection.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass


def normalize_recall(value: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", value.casefold())
                   if not unicodedata.combining(c))


_FILLER = frozenset(
    "a o as os um uma uns umas de do da dos das em no na nos nas sobre acerca "
    "meu minha meus minhas nosso nossa nossos nossas seu sua seus suas "
    "projeto projetos jogo jogos sistema sistemas informacao informacoes "
    "assunto assuntozinho coisa coisas isso isto disso disto aquilo "
    "amora voce vc tu ce me mim que qual quais e com para por".split()
)


def topic_terms(value: str) -> frozenset[str]:
    return frozenset(t for t in re.findall(r"[^\W_]+", normalize_recall(value))
                     if len(t) > 1 and t not in _FILLER)


@dataclass(frozen=True)
class RecallIntent:
    topic: str
    generic: bool = False


_GENERIC = re.compile(
    r"^(?:(?:o\s*que|qual)\s+(?:(?:voce|vc|tu|ce)\s+)?"
    r"(?:(?:lembra|recorda)\s+(?:de mim|sobre mim|das nossas conversas|do que conversamos)"
    r"|(?:a gente\s+|nos\s+)?(?:estavamos\s+)?conversando(?:\s+(?:um tempo atras|antes|ontem|anteriormente))?)"
    r"|(?:(?:voce|vc|tu|ce)\s+)?(?:lembra|recorda)\s+(?:de mim|do que conversamos|das nossas conversas))$"
)
_NAMED = re.compile(
    r"^(?:(?:o\s*que|quais? (?:informacoes|lembrancas))\s+)?"
    r"(?:(?:voce|vc|tu|ce)\s+)?(?:lembra|lembras|recorda|recordas)\s+"
    r"(?:sobre|acerca de|do|da|dos|das|de)\s+(.+)$"
)


def parse_recall_intent(value: str) -> RecallIntent | None:
    raw = value.strip()
    if not raw or raw.startswith(("\"", "'", "“", "«", ">")):
        return None
    text = normalize_recall(raw)
    text = re.sub(r"^amora\s*[,!:]\s*", "", text)
    text = " ".join(text.strip(" ?!.,").split())
    if _GENERIC.fullmatch(text):
        return RecallIntent("", generic=True)
    match = _NAMED.fullmatch(text)
    if not match:
        return None
    topic = match.group(1).strip()
    if len(topic) > 160 or len(topic_terms(topic)) > 12:
        return None
    return RecallIntent(topic)
