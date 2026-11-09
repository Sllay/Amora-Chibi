"""Technical annotation/privacy contracts, independent of live model behavior."""
from amora.identity import Entity, EntityType, Relationship, build_initial_registry
from amora.language import ACIRCompiler, PerspectiveFrame, PerspectiveResolver, PerspectiveStack
from amora.language.text_references import extract_references, history_antecedents, scoped_registry
from amora.cognition.prompt_frame import semantic_acir


def graph():
    registry = build_initial_registry()
    for key in ('RUI', 'LIA', 'MOTHER'):
        registry.add_entity(Entity(id='PERSON_' + key, canonical_name=key.title(), entity_type=EntityType.PERSON))
    registry.add_relationship(Relationship(source='PERSON_MOTHER', relation_type='mother_of', target='PERSON_RUI'))
    return registry


def references(text, registry=None, **kwargs):
    resolver = PerspectiveResolver(registry or build_initial_registry())
    stack = PerspectiveStack(PerspectiveFrame(speaker_id='PERSON_BK', addressee_id='ENTITY_AMORA'))
    return extract_references(text, stack, resolver, **kwargs)


def test_reference_offsets_preserve_typo_and_literal_is_not_trusted():
    text = 'A sua mae mim amar'
    compiler = ACIRCompiler(PerspectiveResolver(build_initial_registry()))
    acir = compiler.compile_utterance(text, PerspectiveStack(PerspectiveFrame(speaker_id='PERSON_BK', addressee_id='ENTITY_AMORA')))
    refs = acir.proposition.roles['references']
    assert [(text[r['start']:r['end']], r['entity_ids']) for r in refs] == [('sua mae', ['ENTITY_CHATGPT']), ('mim', ['PERSON_BK'])]
    assert acir.proposition.roles['assertion_truth'] == 'unverified_user_claim'
    projected = semantic_acir(acir.model_dump(mode='json'))
    assert text not in str(projected)
    assert all('expression' not in r for r in refs)


def test_private_third_party_edge_cannot_resolve_or_disclose_candidates():
    registry = graph()
    public_view = scoped_registry(registry, 'PERSON_BK')
    ref = references('A mãe do Rui me ama', public_view)[0]
    assert ref['status'] == 'unresolved'
    assert ref['entity_ids'] == []
    assert 'PERSON_MOTHER' not in str(ref)
    owner_view = scoped_registry(registry, 'PERSON_RUI')
    assert references('A mãe do Rui me ama', owner_view)[0]['entity_ids'] == ['PERSON_MOTHER']


def test_authorized_canonical_edge_can_be_projected_without_all_graph_edges():
    key = ('PERSON_MOTHER', 'mother_of', 'PERSON_RUI')
    view = scoped_registry(graph(), 'PERSON_BK', authorized_relations={key})
    assert references('A mãe do Rui me ama', view)[0]['entity_ids'] == ['PERSON_MOTHER']
    assert ('ENTITY_CHATGPT', 'mother_of', 'ENTITY_AMORA') in {
        (r.source, r.relation_type, r.target) for r in view.relationships}


def test_report_deixis_does_not_inherit_root_addressee():
    refs = references('Rui disse: "sua mãe me ama"', graph())
    kinship = next(r for r in refs if r['reason_codes'] == ['POSSESSOR_UNKNOWN'])
    assert kinship['status'] == 'unresolved'
    assert kinship['speaker_id'] == 'PERSON_RUI'
    assert kinship['addressee_id'] is None
    assert refs[-1]['entity_ids'] == ['PERSON_RUI']


def test_ambiguous_antecedents_are_not_collapsed_to_newest():
    ref = references('Ela me ama', graph(), antecedents=['PERSON_RUI', 'PERSON_LIA'])[0]
    assert ref['status'] == 'ambiguous'
    assert set(ref['entity_ids']) == {'PERSON_RUI', 'PERSON_LIA'}


def test_history_uses_explicit_referents_not_entire_registry():
    resolver = PerspectiveResolver(graph())
    assert history_antecedents([{'speaker_id': 'PERSON_BK', 'addressee_id': 'ENTITY_AMORA', 'text': 'Sua mãe contou a piada'}], resolver) == ['ENTITY_CHATGPT']
    assert history_antecedents([{'speaker_id': 'PERSON_BK', 'addressee_id': 'ENTITY_AMORA', 'text': 'Oi!'}], resolver) == []


def test_annotation_count_bounded():
    assert len(references(' '.join(['me'] * 1000))) == 32


def test_named_report_addressee_uses_nested_frame_without_root_inheritance():
    text = 'Rui disse a Lia: "sua mãe me ama"'
    refs = references(text, graph())
    mother = next(ref for ref in refs if text[ref['start']:ref['end']] == 'sua mãe')
    assert mother['speaker_id'] == 'PERSON_RUI'
    assert mother['addressee_id'] == 'PERSON_LIA'
    assert mother['entity_ids'] == []  # Graph has no mother for Lia.
    assert refs[-1]['entity_ids'] == ['PERSON_RUI']


def test_narrative_possessive_after_named_third_person_stays_ambiguous():
    ref = next(ref for ref in references('Falei com Lia e sua mãe', graph())
               if ref['reason_codes'] == ['POSSESSIVE_OWNER_AMBIGUOUS'])
    assert ref['status'] == 'ambiguous'
    assert ref['entity_ids'] == []


def test_old_reference_is_not_recovered_across_unrelated_recent_exchange():
    resolver = PerspectiveResolver(graph())
    messages = [{'speaker_id': 'PERSON_BK', 'addressee_id': 'ENTITY_AMORA', 'text': text}
                for text in ['Sua mãe contou uma piada', 'Consertamos o compilador', 'O build passou']]
    assert history_antecedents(messages, resolver) == []


def test_indirect_report_does_not_assume_external_possessive_owner():
    text = 'Ele disse que sua mãe me ama'
    refs = references(text)
    mother = next(ref for ref in refs if text[ref['start']:ref['end']] == 'sua mãe')
    assert mother['status'] == 'ambiguous'
    assert mother['entity_ids'] == []
    # Indirect speech still uses the narrator's first person.
    assert refs[-1]['entity_ids'] == ['PERSON_BK']
