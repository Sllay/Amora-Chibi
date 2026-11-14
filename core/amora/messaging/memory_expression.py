"""Local, fact-preserving wording for already-authorized memory excerpts.

Never fetch another record, consult a personality prompt, or call a model.
Use a small grammatical conversion only for unambiguous owner-attributed
statements. Preserve everything else verbatim, as an attributed quotation.
"""
from __future__ import annotations

import re

from amora.memory.models import ProvenanceKind


_REPORT_PREFIX = re.compile(r"^(?:o falante|a pessoa usu[aá]ria|o usu[aá]rio|a usu[aá]ria)\s+(?=\w)", re.I)
_OWNER_PREFIX = "Relato de PERSON_BK: "
# A small list of unambiguous first-person clauses, not a general paraphraser.
_FIRST_PERSON = re.compile(
    r"^(?:eu\s+)?(estou|estava|tenho|tinha|quero|pretendo|vou|gosto|preciso|criei|comprei|comecei|terminei)\b",
    re.I,
)
_CONJUGATION = {
    "estou": "está", "estava": "estava", "tenho": "tem", "tinha": "tinha",
    "quero": "quer", "pretendo": "pretende", "vou": "vai", "gosto": "gosta",
    "preciso": "precisa", "criei": "criou", "comprei": "comprou",
    "comecei": "começou", "terminei": "terminou",
}
_POSSESSIVES = {"meu": "seu", "meus": "seus", "minha": "sua", "minhas": "suas"}
_POSSESSIVE = re.compile(r"\b(meus?|minhas?)\b", re.I)
_FIRST_POSSESSIVE = re.compile(r"^(meus?|minhas?)\b", re.I)
# Never reassign a pronoun inside reported speech, a quote, or a second
# first-person clause that this limited grammar cannot safely conjugate.
_COMPLEX = re.compile(
    r'["“”«»\n]|\b(?:disse|falou|perguntou|respondeu|contou|segundo|citou|afirmou|escreveu|'
    r'porque|embora|se|quando|enquanto|mas|porém|por que|eu|nós|me|comigo|nosso|nossa|'
    r'me\s+|mim|conosco)\b',
    re.I,
)
_SENTENCE = re.compile(r"(?<=[.!?])\s+")


def _lower_initial(text: str) -> str:
    # Preserve proper nouns, acronyms, and original capitalization when unsure.
    return text[:1].lower() + text[1:] if text and text[0].isupper() and text[1:2].islower() else text


def _replace_possessive(match: re.Match[str]) -> str:
    original = match.group(0)
    result = _POSSESSIVES[original.casefold()]
    return result.capitalize() if original[0].isupper() else result


def _direct_clause(text: str) -> str | None:
    first = _FIRST_PERSON.match(text)
    check = text[first.end():] if first else text
    if _COMPLEX.search(check) or re.search(r"[';:]|\b(?:e|ou)\s+", check, re.I):
        return None
    possessive = _FIRST_POSSESSIVE.match(text)
    if first:
        rest = text[first.end():]
        # Subsequent first-person clauses require real perspective resolution.
        if _FIRST_PERSON.search(rest.strip()) or re.search(
                r"\b(?:e|ou)\s+(?:estou|tenho|quero|vou|gosto|preciso|criei|comecei|terminei)\b", rest, re.I):
            return None
        text = _CONJUGATION[first.group(1).casefold()] + rest
    elif possessive:
        text = _lower_initial(text)
    else:
        return None
    return _POSSESSIVE.sub(_replace_possessive, text)


def memory_clause(record, excerpt: str) -> str:
    """Return conversational attribution without inventing a relationship/fact."""
    source = excerpt.strip()
    if source.startswith(_OWNER_PREFIX):
        source = source[len(_OWNER_PREFIX):].strip()
    owner = getattr(record, "owner_entity_id", None)
    provenance = record.provenance
    # "O falante" is the extraction contract's reporting subject. Resolve it
    # only for an owner-bound cognitive reflection, never arbitrary text.
    if (owner == "PERSON_BK" and provenance.kind == ProvenanceKind.INFERENCE
            and provenance.actor_id == "ENTITY_AMORA"
            and provenance.source.startswith("cognition:")):
        match = _REPORT_PREFIX.match(source)
        if match and not _COMPLEX.search(source[match.end():]):
            return "Você me contou que " + source[match.end():]
    if (owner == "PERSON_BK" and provenance.kind == ProvenanceKind.USER_ASSERTION
            and provenance.actor_id == owner):
        clause = _direct_clause(source)
        if clause is not None:
            # Only the first clause is conjugated; subsequent sentences must
            # not contain a new implicit first-person verb. Use a quote if they do.
            later = _SENTENCE.split(source)[1:]
            if all(re.match(r"^(?:ele|ela|esse sistema|essa ferramenta|esse projeto|o sistema|o projeto)\s+(?:ser[aá]|vai|tem|ter[aá]|[eé])\b", part, re.I)
                   for part in later):
                return "Você me contou que " + clause
        return "Você me contou: “" + source + "”"
    # Other provenance is a stored report/inference, not proof the owner said it.
    return "Tenho guardado isto: “" + source + "”"


def render_memory_reply(records_and_excerpts, *, generic: bool = False) -> str:
    clauses = list(dict.fromkeys(memory_clause(record, excerpt) for record, excerpt in records_and_excerpts))
    if not clauses:
        raise ValueError("MEMORY_EXPRESSION_REQUIRES_RECORD")
    if len(clauses) == 1:
        return ("Lembro sim! " if not generic else "Uma das coisas que lembro é: ") + clauses[0]
    return ("Lembro de algumas coisas. " if generic else "Lembro sim! ") + "\n\n".join(clauses)
