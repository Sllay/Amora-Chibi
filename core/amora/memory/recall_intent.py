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


# Only bounded, non-command conversational lead-ins may be stripped. Never
# scan arbitrary text for an embedded authorization: a quotation, negation,
# hypothetical or reported command must not become a publication grant.
_DISCOURSE_LEAD_IN = re.compile(
    r"^(?:(?:pior\s+que|mas|eu)\s+)?(?:nao|n)\s+(?:tou|to|estou)\s+inventando"
    r"\s*[:,.!]\s+(?=amora\s*[,!:])"
    r"|^(?:sim|certo|beleza|entao|olha|agora|e\s+serio|to\s+falando\s+serio|estou\s+falando\s+serio)"
    r"\s*[:,.!]\s+(?=amora\s*[,!:])"
)


def normalize_memory_request(value: str) -> str | None:
    raw = value.strip()
    if not raw or raw.startswith(('"', "'", "“", "«", ">", "\x60")):
        return None
    normalized = normalize_recall(raw)
    normalized = _DISCOURSE_LEAD_IN.sub("", normalized, count=1)
    normalized = re.sub(r"^amora\s*[,!:]\s*", "", normalized, count=1)
    # Preserve the command boundary: a second paragraph is not automatically
    # an independent authorization unless it followed a known lead-in above.
    return " ".join(normalized.strip(" ?!.,").split())


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
    r"|(?:(?:voce|vc|tu|ce)\s+)?(?:se\s+)?(?:lembra|recorda)\s+"
    r"(?:de mim|do que conversamos|das nossas conversas|do que (?:eu\s+)?(?:disse|falei|contei|comentei)))$"
)
_NAMED = re.compile(
    r"^(?:(?:o\s*que|quais? (?:informacoes|lembrancas))\s+)?"
    r"(?:(?:voce|vc|tu|ce)\s+)?(?:se\s+)?(?:lembra|lembras|recorda|recordas)\s+"
    r"(?:sobre|acerca de|do|da|dos|das|de)\s+(.+)$"
)


_SHORT_RECALL = re.compile(
    r"^(?:e\s+)?a\s+(?:memoria|lembranca)\s+(?:sobre|do|da|dos|das|de)\s+(.+)$"
)
_POLITE_RECALL = re.compile(r"^posso\s+(?:te\s+)?perguntar\s+(?=o\s*que\s+)")
_PRIOR_CHAT_REFERENCE = re.compile(
    r"\s*\?\s*que\s+(?:eu\s+)?(?:te\s+)?(?:falei|contei|disse|comentei)\s+"
    r"(?:no\s+pv|no\s+privado|na\s+dm|em\s+particular)"
    r"(?:\s+(?:um\s+tempinho\s+atras|um\s+tempo\s+atras|antes|ontem|anteriormente))?$"
)


def parse_recall_intent(value: str) -> RecallIntent | None:
    text = normalize_memory_request(value)
    if text is None:
        return None
    text = _POLITE_RECALL.sub("", text, count=1)
    text = _PRIOR_CHAT_REFERENCE.sub("", text, count=1)

    if _GENERIC.fullmatch(text):
        return RecallIntent("", generic=True)
    match = _NAMED.fullmatch(text)
    if not match and value.rstrip().endswith("?"):
        match = _SHORT_RECALL.fullmatch(text)
    if not match:
        return None
    topic = re.sub(r"^que\s+(?:eu\s+)?(?:falei|contei|disse|comentei)\s+(?:sobre|do|da|de)\s+", "", match.group(1)).strip()
    if len(topic) > 160 or len(topic_terms(topic)) > 12:
        return None
    return RecallIntent(topic)
