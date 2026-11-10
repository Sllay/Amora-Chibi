"""Local discourse attribution for intent routing, never a general semantic parser.

Segments keep literal offsets, speaker and target. Reporting clauses are data;
only directly addressed segments can grant personal-choice authorization.
Ambiguous attribution withholds that grant, not conversational expression.
"""
from dataclasses import dataclass
import re
import unicodedata

VERSION = 'amora.addressing.v1'
REPORT_VERBS = frozenset(('diz disse fala falou solta pergunta perguntou perguntei '
                         'responde respondeu devolve rebate zoa provoca comenta comentou').split())
FIRST_PERSON = frozenset(('eu me mim comigo minha meu minhas meus').split())
SECOND_PERSON = frozenset(
    ('tu voce vc ce oce te ti contigo teu tua teus tuas '
     'seu sua seus suas voces vcs ces').split()
)
SECOND_PERSON_SUBJECTS = frozenset(('tu voce vc ce oce voces vcs ces').split())


def normalized(text: str) -> str:
    return ''.join(c for c in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(c))


def words(text: str) -> list[str]:
    return re.findall(r'\w+', normalized(text))


@dataclass(frozen=True)
class AddressedSegment:
    start: int
    end: int
    speaker: str | None
    target: str | None
    kind: str


@dataclass(frozen=True)
class Addressing:
    addressed_text: str
    reported: bool
    segments: tuple[AddressedSegment, ...]


def _report_target(prefix: str, speaker: str, target: str | None) -> tuple[str | None, str | None]:
    tokens = words(prefix)
    verb = next((i for i, token in enumerate(tokens) if token in REPORT_VERBS), None)
    if verb is None:
        # Named dialogue label, as in Rui: "...". Bare lexical quotations stay input.
        return (None, None) if prefix.rstrip().endswith(':') else (speaker, target)
    before, after = tokens[:verb], tokens[verb + 1:]
    recipient = any(after[i] in {'a', 'ao', 'para', 'pra'} and after[i + 1] == 'amora'
                    for i in range(len(after) - 1))
    if (not before or before in (['me'], ['amora', 'me']) or before == ['amora']
            or before[0] in {'eu', 'tu', 'voce', 'vc', 'ce', 'oce', 'voces', 'vcs', 'ces'}
            or (any(t in FIRST_PERSON for t in before) and ('te' in before or recipient))):
        # "Você falou comigo?" is still spoken by the current speaker to
        # Amora. The reporting verb alone is not evidence of nested speech.
        # A genuinely named source ("Rui disse: ...") stays reported.
        return speaker, target
    return None, None


def _attribute(raw: str, speaker: str, target: str | None, tail: str = ''):
    quote = re.search(r'["“‘\']', raw)
    prefix = raw[:quote.start()] if quote else raw
    if quote and not prefix.strip():
        close = {'“': '”', '‘': '’'}.get(quote.group(), quote.group())
        end = raw.find(close, quote.start() + 1)
        suffix = (raw[end + 1:] if end >= 0 else '') + tail
        if any(t in REPORT_VERBS for t in words(suffix)):
            prefix = suffix
    return _report_target(prefix, speaker, target)


def analyze_addressing(text: str, speaker_id: str, target_id: str | None) -> Addressing:
    # A bounded scanner, not a list of regexes for individual stories. A report
    # consumes its clause; later independent clauses can address Amora again.
    spans: list[AddressedSegment] = []
    start = 0
    quoted = None
    for index, char in enumerate(text):
        if quoted:
            if char == quoted:
                quoted = None
                if index and text[index - 1] in '.!?':
                    raw = text[start:index + 1]
                    quote = re.search(r'["“‘\']', raw)
                    actor, target = _attribute(raw, speaker_id, target_id, text[index + 1:].split('.')[0])
                    kind = 'direct' if actor == speaker_id and target == target_id else 'reported'
                    spans.append(AddressedSegment(start, index + 1, actor, target, kind))
                    start = index + 1
        elif char in {'"', "'", '“', '‘'}:
            quoted = {'“': '”', '‘': '’'}.get(char, char)
        elif char in '.!?;\n':
            raw = text[start:index + 1]
            quote = re.search(r'["“‘\']', raw)
            prefix = raw[:quote.start()] if quote else raw
            actor, target = _attribute(raw, speaker_id, target_id)
            kind = 'direct' if actor == speaker_id and target == target_id else 'reported'
            spans.append(AddressedSegment(start, index + 1, actor, target, kind))
            start = index + 1
    if start < len(text):
        raw = text[start:]
        quote = re.search(r'["“‘\']', raw)
        actor, target = _attribute(raw, speaker_id, target_id)
        kind = 'direct' if actor == speaker_id and target == target_id else 'reported'
        spans.append(AddressedSegment(start, len(text), actor, target, kind))
    addressed = ' '.join(text[s.start:s.end] for s in spans if s.kind == 'direct')
    return Addressing(addressed, any(s.kind == 'reported' for s in spans), tuple(spans))


def personal_choice_requested(text: str) -> bool:
    tokens = words(text)
    # Explicit second-person question, imperative, or gender/body choice query.
    # 'Eu gosto...' alone is a speaker disclosure, not a choice for Amora.
    if not tokens:
        return False
    if tokens[0] in {'escolhe', 'decide', 'opta'}:
        return True
    # A speaker's own disclosure is not a request, even if it mentions 'you'.
    if tokens[0] == 'eu' and not any(t in REPORT_VERBS or t in {'escolha', 'escolhe'} for t in tokens):
        return False
    question = '?' in text or tokens[0] in {'qual', 'que', 'de', 'como', 'e'} | SECOND_PERSON_SUBJECTS
    if question and any(t in SECOND_PERSON for t in tokens):
        return True
    return tokens[0] in {'qual', 'que'} and any(t.startswith(('genero', 'escolher')) for t in tokens)
