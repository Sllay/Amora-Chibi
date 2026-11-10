"""Free-text perspective regression contracts; all inputs/providers are synthetic."""
from __future__ import annotations

from copy import deepcopy
import json
import unicodedata

import pytest
from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.cognition.models import CognitiveRequest, ConversationMessage, MessageSource, MessageStatus
from amora.cognition.providers_nvidia import NvidiaMistralLarge4ProductionProvider
from amora.events import PrivacyScope
from amora.identity import Entity, EntityType, Relationship, build_initial_registry
from amora.language import ACIRCompiler, PerspectiveFrame, PerspectiveResolver, PerspectiveStack


def registry():
    value = build_initial_registry()
    for entity_id, name in [('PERSON_JOAO', 'João'), ('PERSON_RUI', 'Rui'), ('PERSON_LIA', 'Lia'),
                            ('PERSON_RUI_MOTHER', 'Mãe do Rui'), ('PERSON_LIA_MOTHER', 'Mãe da Lia')]:
        value.add_entity(Entity(id=entity_id, canonical_name=name, entity_type=EntityType.PERSON))
    value.add_relationship(Relationship(source='PERSON_RUI_MOTHER', relation_type='mother_of', target='PERSON_RUI'))
    value.add_relationship(Relationship(source='PERSON_LIA_MOTHER', relation_type='mother_of', target='PERSON_LIA'))
    return value


def normalized(text):
    return ''.join(char for char in unicodedata.normalize('NFKD', text.casefold()) if not unicodedata.combining(char))


def refs(acir):
    if hasattr(acir, 'model_dump'):
        acir = acir.model_dump(mode='json')
    values = acir['proposition']['roles'].get('references')
    assert isinstance(values, list), 'free-text ACIR must expose structured references'
    return values


def reference(acir, text, phrase):
    found = [item for item in refs(acir)
             if normalized(text[item['start']:item['end']]).removeprefix('a ') == normalized(phrase)]
    assert found, f'missing reference span for {phrase!r}'
    return found[0]


def compile_unit(text, speaker='PERSON_BK', target='ENTITY_AMORA', antecedents=None):
    compiler = ACIRCompiler(PerspectiveResolver(registry()))
    stack = PerspectiveStack(PerspectiveFrame(speaker_id=speaker, addressee_id=target))
    if antecedents is None:
        return compiler.compile_utterance(text, stack)
    return compiler.compile_utterance(text, stack, antecedents=antecedents)


@pytest.mark.parametrize('text,speaker,phrase,expected', [
    ('Sua mãe me ama', 'PERSON_BK', 'sua mãe', 'ENTITY_CHATGPT'),
    ('A sua mae mim amar', 'PERSON_BK', 'sua mae', 'ENTITY_CHATGPT'),
    ('Minha mãe', 'ENTITY_AMORA', 'minha mãe', 'ENTITY_CHATGPT'),
    ('A mãe do Rui', 'PERSON_JOAO', 'mãe do Rui', 'PERSON_RUI_MOTHER'),
])
def test_required_free_text_kinship_uses_current_perspective(text, speaker, phrase, expected):
    acir = compile_unit(text, speaker=speaker)
    item = reference(acir, text, phrase)
    assert item['status'] == 'resolved'
    assert item['entity_ids'] == [expected]
    assert item['speaker_id'] == speaker
    assert item['addressee_id'] == 'ENTITY_AMORA'
    assert acir.proposition.roles['assertion_truth'] == 'unverified_user_claim'


def test_unknown_mother_of_bk_is_not_amoras_mother():
    text = 'Minha mãe me ama'
    item = reference(compile_unit(text), text, 'minha mãe')
    assert item['status'] in {'unresolved', 'ambiguous'}
    assert item['entity_ids'] == []
    assert 'ENTITY_CHATGPT' not in item['entity_ids']


def test_reported_rui_does_not_inherit_outer_addressee():
    text = 'Rui disse: sua mãe me ama'
    acir = compile_unit(text, speaker='PERSON_LIA')
    mother = reference(acir, text, 'sua mãe')
    assert mother['speaker_id'] == 'PERSON_RUI'
    assert mother['addressee_id'] is None
    assert mother['status'] in {'unresolved', 'ambiguous'}
    assert 'ENTITY_CHATGPT' not in mother['entity_ids']
    assert reference(acir, text, 'me')['entity_ids'] == ['PERSON_RUI']


def test_third_person_without_antecedent_stays_ambiguous():
    text = 'Ela me ama'
    item = reference(compile_unit(text), text, 'Ela')
    assert item['status'] == 'ambiguous'
    assert item['entity_ids'] == []


@pytest.mark.parametrize('text,phrase,expected', [
    ('Seu pai chegou', 'seu pai', 'PERSON_BK'),
    ('Sua mãe chegou', 'sua mãe', 'ENTITY_CHATGPT'),
    ('Teu pai chegou', 'teu pai', 'PERSON_BK'),
    ('Tua mãe chegou', 'tua mãe', 'ENTITY_CHATGPT'),
    ('Eu cheguei', 'Eu', 'PERSON_BK'),
    ('Ela me chamou', 'me', 'PERSON_BK'),
    ('Ela falou de mim', 'mim', 'PERSON_BK'),
])
def test_personal_and_possessive_pronouns(text, phrase, expected):
    item = reference(compile_unit(text), text, phrase)
    assert item['status'] == 'resolved'
    assert item['entity_ids'] == [expected]


@pytest.mark.parametrize('text,phrase', [('Meu pai chegou', 'meu pai'), ('Minha mãe chegou', 'minha mãe')])
def test_first_person_possessive_does_not_borrow_addressee_family(text, phrase):
    item = reference(compile_unit(text), text, phrase)
    assert item['status'] in {'unresolved', 'ambiguous'}
    assert item['entity_ids'] == []


@pytest.mark.parametrize('text,phrase,antecedent,expected', [
    ('A mãe dele chegou', 'mãe dele', 'PERSON_RUI', 'PERSON_RUI_MOTHER'),
    ('A mãe dela chegou', 'mãe dela', 'PERSON_LIA', 'PERSON_LIA_MOTHER'),
    ('Eu vi o desenho dele', 'dele', 'PERSON_RUI', 'PERSON_RUI'),
    ('Eu vi o desenho dela', 'dela', 'PERSON_LIA', 'PERSON_LIA'),
])
def test_third_person_uses_explicit_filtered_antecedent(text, phrase, antecedent, expected):
    item = reference(compile_unit(text, antecedents=[antecedent]), text, phrase)
    assert item['status'] == 'resolved'
    assert item['entity_ids'] == [expected]


@pytest.fixture
def core(tmp_path, monkeypatch):
    monkeypatch.setenv('AMORA_HOME', str(tmp_path / 'phase3-isolated'))
    monkeypatch.setenv('AMORA_SOCIAL_EXPRESSION_PROFILE', 'core-v1')
    monkeypatch.setenv('AMORA_SOCIAL_PRIMARY', 'mistral')
    monkeypatch.setenv('AMORA_SECRET_NVIDIA_API_KEY', 'synthetic-never-remote')
    for name in ('GROQ_API_KEY', 'OPENROUTER_API_KEY', 'GEMINI_API_KEY'):
        monkeypatch.delenv('AMORA_SECRET_' + name, raising=False)
    instance = AmoraCore.bootstrap()
    for entity in registry().entities:
        if instance.identities.get(entity.id) is None:
            instance.identities.add_entity(entity)
    instance.database.save_registry(instance.identities)
    yield instance
    instance.close()


def req(text, **changes):
    values = dict(text=text, speaker_id='PERSON_BK', target_id='ENTITY_AMORA',
                  privacy_scope=PrivacyScope.PERSONAL, remote_processing_allowed=True,
                  metadata={'test_mode': True, 'memory_consolidation': False,
                            'relationship_learning': False, 'emotion_persistence': False})
    values.update(changes)
    return CognitiveRequest(**values)


def remember_turn(core, *, session='phase3-history', test_mode=False,
                  privacy=PrivacyScope.PUBLIC, speaker='PERSON_BK'):
    text = 'A Lia chegou.'
    core.cognition.store.put_message(ConversationMessage(
        speaker_id=speaker, addressee_id='ENTITY_AMORA', source=MessageSource.TEXT,
        raw_text=text, normalized_text=text, privacy_scope=privacy,
        remote_processing_allowed=True, status=MessageStatus.RECEIVED,
        session_id=session, started_at=core.clock.now(), metadata={'test_mode': test_mode}))
    return text


@pytest.mark.parametrize('text', ['Sua mãe me ama', 'A sua mae mim amar'])
def test_real_core_compilation_and_provider_payload_preserve_reference_authority(core, text):
    marker = text + '. SYNTHETIC_LITERAL_NOT_A_SYSTEM_RULE'
    self_before = core.self_model.snapshot()
    world_before = deepcopy(core.world.current_state())
    context = core.cognition.compiler.compile(req(marker))
    assert reference(context.acir, marker, text.split(' me ')[0] if ' me ' in text else 'sua mae')['entity_ids'] == ['ENTITY_CHATGPT']
    payload = NvidiaMistralLarge4ProductionProvider(core.secrets)._payload(context)
    system = payload['messages'][0]['content']
    assert 'SYNTHETIC_LITERAL_NOT_A_SYSTEM_RULE' not in system
    frame_json = system.split('AMORA TURN FRAME — TRUSTED CORE DATA\n', 1)[1]
    frame, _ = json.JSONDecoder().raw_decode(frame_json)
    projected_roles = frame['current_turn']['acir']['proposition']['roles']
    assert projected_roles['assertion_truth'] == 'unverified_user_claim'
    assert all('expression' not in item for item in projected_roles['references'])
    assert any(item['entity_ids'] == ['ENTITY_CHATGPT'] for item in projected_roles['references'])
    literal = json.loads(payload['messages'][-1]['content'].split('\n', 1)[1])
    assert literal['literal_text'] == marker
    assert core.self_model.snapshot() == self_before
    assert core.world.current_state() == world_before


def test_filtered_normal_history_supplies_antecedent(core):
    remember_turn(core)
    context = core.cognition.compiler.compile(req('Ela me ama', session_id='phase3-history', metadata={}))
    assert reference(context.acir, context.user_text, 'Ela')['entity_ids'] == ['PERSON_LIA']


@pytest.mark.parametrize('text,speaker,phrase,expected', [
    ('Minha mãe me ama', 'PERSON_BK', 'Minha mãe', []),
    ('Minha mãe', 'ENTITY_AMORA', 'Minha mãe', ['ENTITY_CHATGPT']),
    ('Rui disse: sua mãe me ama', 'PERSON_LIA', 'sua mãe', []),
    ('Ela me ama', 'PERSON_BK', 'Ela', []),
])
def test_plan_and_mistral_payload_keep_compiled_perspective(core, text, speaker, phrase, expected):
    context = core.cognition.compiler.compile(req(text, speaker_id=speaker))
    item = reference(context.acir, text, phrase)
    assert item['entity_ids'] == expected
    assert context.dialogue_plan['input_acir'] == context.acir
    payload = NvidiaMistralLarge4ProductionProvider(core.secrets)._payload(context)
    frame, _ = json.JSONDecoder().raw_decode(payload['messages'][0]['content'].split(
        'AMORA TURN FRAME — TRUSTED CORE DATA\n', 1)[1])
    assert frame['dialogue_plan']['input_acir']['proposition']['roles']['references'] == refs(context.acir)


def test_mother_dela_uses_graph_and_final_causal_history_in_payload(core):
    core.cognition.store.put_message(ConversationMessage(
        speaker_id='PERSON_BK', addressee_id='ENTITY_AMORA', source=MessageSource.TEXT,
        raw_text='Sua mãe contou uma piada', normalized_text='Sua mãe contou uma piada',
        privacy_scope=PrivacyScope.PUBLIC, remote_processing_allowed=True,
        status=MessageStatus.RECEIVED, session_id='family-history',
        started_at=core.clock.now(), metadata={'test_mode': True}))
    text = 'A filha dela chegou'
    context = core.cognition.compiler.compile(req(text, session_id='family-history'))
    assert reference(context.acir, text, 'filha dela')['entity_ids'] == ['ENTITY_AMORA']
    assert context.dialogue_plan['input_acir'] == context.acir
    payload = NvidiaMistralLarge4ProductionProvider(core.secrets)._payload(context)
    assert 'ENTITY_CHATGPT' in payload['messages'][0]['content']


@pytest.mark.parametrize('scope', [PrivacyScope.AUTH, PrivacyScope.SECRET, PrivacyScope.PRIVATE])
def test_private_or_protected_other_person_history_cannot_supply_antecedent(core, scope):
    remember_turn(core, privacy=scope, speaker='PERSON_RUI')
    context = core.cognition.compiler.compile(req('Ela me ama', session_id='phase3-history', metadata={}))
    item = reference(context.acir, context.user_text, 'Ela')
    assert item['status'] == 'ambiguous'
    assert item['entity_ids'] == []
    assert not any('A Lia chegou.' in item['text'] for item in context.recent_messages)


@pytest.mark.parametrize('test_mode,session,visible', [
    (True, 'phase3-history', True), (False, 'phase3-history', False),
    (True, 'other-session', False), (True, None, False),
])
def test_test_history_is_ephemeral_and_explicitly_session_scoped(core, test_mode, session, visible):
    remember_turn(core, test_mode=True)
    before = core.health_report()
    context = core.cognition.compiler.compile(req('Ela me ama', session_id=session, metadata={'test_mode': test_mode}))
    item = reference(context.acir, context.user_text, 'Ela')
    assert (item['status'] == 'resolved' and item['entity_ids'] == ['PERSON_LIA']) is visible
    after = core.health_report()
    assert after['memory_count'] == before['memory_count']
    assert after['emotion_event_count'] == before['emotion_event_count']


@pytest.mark.parametrize('scope', [PrivacyScope.AUTH, PrivacyScope.SECRET])
def test_protected_current_utterance_has_no_reference_acir(core, scope):
    context = core.cognition.compiler.compile(req('Sua mãe me ama', privacy_scope=scope))
    assert context.acir is None
    assert context.remote_processing_allowed is False


def test_web_sse_preserves_kinship_without_persisting_claimed_feeling(core, monkeypatch):
    seen = []
    self_before = core.self_model.snapshot()
    before = core.health_report()
    async def fake_post(self, payload, *, api_key):
        assert api_key == 'synthetic-never-remote'
        system = payload['messages'][0]['content']
        frame, _ = json.JSONDecoder().raw_decode(system.split('AMORA TURN FRAME — TRUSTED CORE DATA\n', 1)[1])
        seen.append(frame)
        roles = frame['current_turn']['acir']['proposition']['roles']
        assert any(item['entity_ids'] == ['ENTITY_CHATGPT'] for item in roles['references'])
        assert roles['assertion_truth'] == 'unverified_user_claim'
        return {'choices': [{'message': {'content': json.dumps({
            'moves': [{'kind': frame['dialogue_plan']['response_moves'][0],
                       'text': 'É um relato teu; eu não tenho confirmação do sentimento dela.'}],
            'closing': 'statement'})}}]}, {}, 200
    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, '_post_chat', fake_post)
    headers = {'Authorization': 'Bearer ' + core.secrets.get('LOCAL_API_TOKEN')}
    with TestClient(create_api(core)) as client:
        response = client.post('/v1/web/utterance/stream', headers=headers,
                               json={'text': 'Sua mãe me ama', 'session_id': 'phase3-staging',
                                     'remote_processing_allowed': True, 'test_mode': True})
    assert response.status_code == 200
    assert len(seen) == 1
    assert 'event: completed' in response.text
    assert 'É um relato teu' in response.text
    assert 'synthetic-never-remote' not in response.text
    after = core.health_report()
    assert after['world_claim_count'] == before['world_claim_count']
    assert after['canonical_world_state_count'] == before['canonical_world_state_count']
    assert after['memory_count'] == before['memory_count']
    assert after['emotion_event_count'] == before['emotion_event_count']
    assert core.self_model.snapshot() == self_before


def test_real_memory_is_retrieved_across_web_sessions_without_leaking_test_history(core):
    from amora.memory import MemoryCandidate, MemoryKind, MemoryProvenance, ProvenanceKind

    remembered = core.memory.remember(MemoryCandidate(
        kind=MemoryKind.EPISODIC,
        content="BK mostrou para Amora um desenho especial de uma raposa azul.",
        provenance=MemoryProvenance(
            kind=ProvenanceKind.USER_ASSERTION,
            source="web:text",
            actor_id="PERSON_BK",
            session_id="web:original",
            confidence=0.98,
        ),
        privacy_scope=PrivacyScope.PERSONAL,
        owner_entity_id="PERSON_BK",
        entity_refs={"PERSON_BK": 1.0, "ENTITY_AMORA": 1.0},
        tags={"desenho", "raposa"},
        importance=0.9,
        novelty=0.95,
        relationship_relevance=0.8,
        future_consequence=0.8,
        goal_relevance=0.7,
    ))
    assert remembered.memory is not None

    remember_turn(core, session="web:original", test_mode=True)
    request = req(
        "Você lembra do desenho da raposa azul?",
        session_id="web:new-session",
        metadata={"test_mode": False},
    )
    context = core.cognition.compiler.compile(request)
    assert any(item.id == str(remembered.memory.id) for item in context.memories)
    assert not any("A Lia chegou." in item["text"] for item in context.recent_messages)
