"""Bounded reference annotation, not semantic truth or relationship learning.

Offsets refer to the original utterance. Literal fragments never enter the
trusted annotation; unresolved deixis stays unresolved instead of guessing.
"""
from __future__ import annotations

import re
from typing import Any

from amora.identity import EntityRegistry, build_initial_registry
from .attribution import analyze_addressing, normalized, REPORT_VERBS
from .perspective import PerspectiveFrame, PerspectiveResolver, PerspectiveStack

PUBLIC_AMORA_RELATIONS = frozenset(
    (item.source, item.relation_type, item.target)
    for item in build_initial_registry().relationships
    if item.relation_type in {'father_of', 'mother_of', 'child_of'}
)
KINSHIP = frozenset({'mae', 'pai', 'filha', 'filho'})
POSSESSIVES = frozenset('minha meu minhas meus sua seu suas seus tua teu tuas teus'.split())
# These forms are compared after accent folding by attribution.normalized.
# Collective/plural "vocês" and reflexive/conditional "se" deliberately remain
# uncertain unless the local syntax supplies a safe referent.
PRONOUNS = frozenset(
    'eu me mim comigo voce vc tu ce oce te ti contigo voces vcs ces vos '
    'ele ela dele dela se'.split()
)
SECOND_SUBJECT = frozenset('voce vc tu ce oce'.split())
REFLEXIVE_ADVERBS = frozenset('nao ja ainda tambem'.split())
TOKEN = re.compile(r'\w+', re.UNICODE)
MAX_REFERENCES = 32
MAX_TOKENS = 2048


def scoped_registry(registry: EntityRegistry, recipient_id: str, *, authorized_relations=(), include_recipient_edges: bool = True) -> EntityRegistry:
    """Relationship graph has no privacy labels: default-deny third-party edges.

    Canonical Amora parenthood is an explicit public catalogue, not permission
    to disclose every edge mentioning Amora. Extra edges require independently
    audience-approved canonical world claims from the caller.
    """
    result = EntityRegistry()
    for entity in registry.entities:
        result.add_entity(entity.model_copy(deep=True))
    allowed = set(authorized_relations)
    for relation in registry.relationships:
        key = (relation.source, relation.relation_type, relation.target)
        if (include_recipient_edges and recipient_id in {relation.source, relation.target}) or key in PUBLIC_AMORA_RELATIONS or key in allowed:
            result.add_relationship(relation.model_copy(deep=True))
    return result


def _reflexive_subject(tokens, index):
    # The reflexive "se" must have an explicit subject in this small local
    # window; "se eu quiser" and "perguntou se..." are conditionals, not
    # references. Never infer a deictic owner from the token alone.
    previous = normalized(tokens[index - 1][0]) if index else ''
    if previous in REFLEXIVE_ADVERBS and index > 1:
        previous = normalized(tokens[index - 2][0])
    return previous


def _alias_at(tokens, index, registry):
    # Longest matching registered alias, at most eight lexical words. Token
    # boundaries avoid substring attribution (e.g. Rui inside another name).
    for end in range(min(len(tokens), index + 8), index, -1):
        name = ' '.join(token[0] for token in tokens[index:end])
        entity = registry.resolve_alias(name)
        if entity:
            return entity.id, end
    return None, index


def extract_references(text: str, stack: PerspectiveStack, resolver: PerspectiveResolver,
                       *, antecedents: list[str] | None = None) -> list[dict[str, Any]]:
    root = stack.current
    annotations = []
    candidates = list(dict.fromkeys(antecedents or ()))
    addressing = analyze_addressing(text, root.speaker_id, root.addressee_id)
    for segment in addressing.segments:
        raw = text[segment.start:segment.end]
        tokens = [(match.group(), segment.start + match.start(), segment.start + match.end())
                  for match in TOKEN.finditer(raw)][:MAX_TOKENS]
        actor, target = segment.speaker, segment.target
        explicit_report = segment.kind == 'reported' and any(char in raw for char in '"“‘\':')
        if segment.kind == 'reported' and not explicit_report:
            # Narration keeps the narrator's deixis: 'Ela falou de mim' does
            # not turn 'mim' into the reported person's first person.
            actor, target = root.speaker_id, root.addressee_id
        if explicit_report:
            # Only a named reporting subject licenses a nested speaker. The
            # root addressee is never silently inherited inside another voice.
            report_index = next((i for i, token in enumerate(tokens)
                                 if normalized(token[0]) in REPORT_VERBS), None)
            if report_index is not None:
                names = []
                for i in range(report_index):
                    entity_id, _ = _alias_at(tokens, i, resolver.registry)
                    if entity_id:
                        names.append(entity_id)
                if len(set(names)) == 1:
                    actor = names[0]
            target = None
            if report_index is not None:
                # A recipient needs an explicit reporting-clause preposition;
                # names merely mentioned inside the quote are not addressees.
                boundary = min((raw.find(char) for char in '"“‘\':' if char in raw), default=len(raw)) + segment.start
                recipients = []
                for i in range(report_index + 1, len(tokens) - 1):
                    if tokens[i][1] >= boundary:
                        break
                    if normalized(tokens[i][0]) in {'a', 'ao', 'para', 'pra'}:
                        recipient, _ = _alias_at(tokens, i + 1, resolver.registry)
                        if recipient:
                            recipients.append(recipient)
                if len(set(recipients)) == 1:
                    target = recipients[0]
        local = PerspectiveStack(PerspectiveFrame(speaker_id=actor or '__UNKNOWN_REPORTED_SPEAKER__',
                                                  addressee_id=target))
        index = 0
        named_mentions = []
        while index < len(tokens) and len(annotations) < MAX_REFERENCES:
            word, start, _ = tokens[index]
            key = normalized(word)
            end = index + 1
            expression = None
            entity_id = None
            named_owner = None
            if key in POSSESSIVES and end < len(tokens) and normalized(tokens[end][0]) in KINSHIP:
                end += 1
                expression = ' '.join(t[0] for t in tokens[index:end])
            elif key in KINSHIP and end < len(tokens) and normalized(tokens[end][0]) in {'dele', 'dela'}:
                end += 1
                expression = ' '.join(t[0] for t in tokens[index:end])
            elif key in KINSHIP and end < len(tokens) and normalized(tokens[end][0]) in {'de', 'do', 'da'}:
                named_owner, named_end = _alias_at(tokens, end + 1, resolver.registry)
                if named_owner:
                    end = named_end
                    expression = 'minha ' + word
            elif key in POSSESSIVES:
                # Even without a family noun, ownership is relevant ("teu
                # desenho", "minha casa"). Only the possessor is resolved.
                expression = word
            elif key == 'se':
                # "Se você quiser" is conditional, never a pronoun naming
                # Amora. Reflexive "tu se machucou" has an explicit subject.
                previous = _reflexive_subject(tokens, index)
                if previous in SECOND_SUBJECT or previous in {'ele', 'ela'}:
                    expression = word
            elif key in PRONOUNS:
                expression = word
            else:
                # Bare relational nouns like "mãe" are not personal aliases;
                # assigning them to one registry entry would leak identity.
                if key not in KINSHIP:
                    entity_id, alias_end = _alias_at(tokens, index, resolver.registry)
                    if entity_id:
                        end = alias_end
                        expression = resolver.registry.get(entity_id).canonical_name
            if expression is not None:
                frame = local
                if named_owner:
                    frame = PerspectiveStack(PerspectiveFrame(speaker_id=named_owner))
                resolution = resolver.resolve(expression, frame, candidates=candidates)
                ids = [value for value in resolution.entity_ids if value != '__UNKNOWN_REPORTED_SPEAKER__']
                status = resolution.status.value
                reasons = list(resolution.reason_codes)
                if key == 'se':
                    previous = _reflexive_subject(tokens, index)
                    if previous in SECOND_SUBJECT and target:
                        status, ids, reasons = 'resolved', [target], ['EXPLICIT_SECOND_PERSON_REFLEXIVE']
                    else:
                        status, ids, reasons = 'ambiguous', [], ['REFLEXIVE_SUBJECT_UNRESOLVED']
                if key in {'ele', 'ela', 'dele', 'dela'} and not ids and status == 'unresolved':
                    status = 'ambiguous'
                if key in {'sua', 'seu', 'suas', 'seus'} and (
                        any(mention not in {actor, target} for mention in named_mentions)
                        or any(normalized(item[0]) in {'ele', 'ela'}
                               for item in tokens[:index])):
                    # Brazilian "seu/sua" can mean "his/her" after an explicit
                    # third-person subject. Preserve uncertainty rather than
                    # automatically assigning the addressee's property.
                    status, ids, reasons = 'ambiguous', [], ['POSSESSIVE_OWNER_AMBIGUOUS']
                if (key in {'sua', 'seu', 'suas', 'seus'}
                        and segment.kind == 'reported' and not explicit_report):
                    # In indirect speech, third-person possessives may belong
                    # to the reported subject or to the outer addressee. Keep
                    # narrator first person, but never guess this ownership.
                    status, ids, reasons = 'ambiguous', [], ['REPORTED_POSSESSOR_AMBIGUOUS']
                if actor is None and not named_owner and not entity_id and key in {'eu', 'me', 'mim', 'comigo'}:
                    status, ids, reasons = 'unresolved', [], ['REPORTED_SPEAKER_UNKNOWN']
                if named_owner:
                    reasons.append('EXPLICIT_NAMED_POSSESSOR')
                annotations.append({'start': start, 'end': tokens[end - 1][2], 'status': status,
                                    'entity_ids': ids, 'reason_codes': reasons,
                                    'speaker_id': actor, 'addressee_id': target})
                if status == 'resolved' and ids:
                    # An explicit mention updates the local antecedents; first/
                    # second-person deixis is not a third-person antecedent.
                    if key not in PRONOUNS:
                        candidates = list(dict.fromkeys(candidates + ids))
                    if entity_id:
                        named_mentions.extend(ids)
            index = end
    return annotations


def history_antecedents(messages: list[dict], resolver: PerspectiveResolver) -> list[str]:
    """Use only caller-filtered history, preserving uncertainty among mentions.

    Only the newest two messages are eligible: ContextCompiler's budget fitter
    retains this causal exchange. Older references cannot survive removal of
    their supporting history or bridge an unrelated topic by accident.
    """
    for message in reversed(messages[-2:]):
        speaker = message.get('speaker_id')
        if not speaker:
            continue
        frame = PerspectiveStack(PerspectiveFrame(speaker_id=speaker, addressee_id=message.get('addressee_id')))
        refs = extract_references(message.get('text', ''), frame, resolver)
        ids = []
        text = message.get('text', '')
        for ref in refs:
            fragment = normalized(text[ref['start']:ref['end']])
            if ref['status'] == 'resolved' and fragment not in PRONOUNS:
                ids.extend(ref['entity_ids'])
        if ids:
            return list(dict.fromkeys(ids))
    return []
