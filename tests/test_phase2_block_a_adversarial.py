"""Adversarial boundaries for Phase 2/A; fake markers, no remote generation."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from amora.app import AmoraCore
from amora.api import create_api
from amora.cognition.dialogue import DialogueOrchestrator
from amora.cognition.models import CognitiveRequest
from amora.cognition.realization import RealizationValidator, SelfChoiceProposal
from amora.cognition.self_choice import commit_self_choices
from amora.events import PrivacyScope
from amora.world import SelfLayer


@pytest.fixture
def core(tmp_path, monkeypatch):
    monkeypatch.setenv('AMORA_HOME', str(tmp_path / 'runtime'))
    for name in ('NVIDIA_API_KEY', 'GROQ_API_KEY', 'OPENROUTER_API_KEY', 'GEMINI_API_KEY'):
        monkeypatch.delenv('AMORA_SECRET_' + name, raising=False)
    instance = AmoraCore.bootstrap()
    yield instance
    instance.close()


def request(text='Qual gênero tu quer escolher?', **changes):
    fields = dict(text=text, speaker_id='PERSON_BK', target_id='ENTITY_AMORA',
                  privacy_scope=PrivacyScope.PERSONAL, remote_processing_allowed=True,
                  metadata={'memory_consolidation': False})
    fields.update(changes)
    return CognitiveRequest(**fields)


def choice():
    return SelfChoiceProposal(key='gender_identity', value='SYNTHETIC_SELF_CHOICE')


@pytest.mark.parametrize('scope', [PrivacyScope.AUTH, PrivacyScope.SECRET])
@pytest.mark.parametrize('injected', [False, True])
async def test_protected_observation_redacts_return_event_and_subscribers(core, scope, injected):
    marker = 'SYNTHETIC_BOUNDARY_MARKER_' + scope.value
    observed = []
    async def capture(event):
        observed.append(event.model_dump_json())
    await core.events.subscribe('language.utterance.observed', capture)
    before_events = core.database.event_count()
    before_messages = core.cognition.store.message_count()
    extra = {'text': marker, 'nested': {'body': marker}, 'raw_text': marker} if injected else None
    observation = await core.language_social.observe_text(
        marker, speaker_id='PERSON_BK', explicit_addressee_id='ENTITY_AMORA',
        privacy_scope=scope, extra_payload=extra)
    assert observation.text is None
    assert marker not in observation.model_dump_json()
    assert observed and all(marker not in item for item in observed)
    assert core.database.event_count() == before_events
    assert core.cognition.store.message_count() == before_messages
    later = core.cognition.compiler.compile(request('O que a gente estava conversando?'))
    assert marker not in json.dumps(later.recent_messages)
    # A fresh compiler must not recover a protected observation from history.
    assert marker not in later.model_dump_json()


@pytest.mark.parametrize('scope', [PrivacyScope.AUTH, PrivacyScope.SECRET])
def test_web_protected_input_never_reaches_response_history_or_event_storage(core, scope):
    marker = 'SYNTHETIC_WEB_BOUNDARY_' + scope.value
    headers = {'Authorization': 'Bearer ' + core.secrets.get('LOCAL_API_TOKEN')}
    before = core.database.event_count()
    with TestClient(create_api(core)) as client:
        response = client.post('/v1/web/utterance', headers=headers,
                               json={'text': marker, 'privacy_scope': scope.value,
                                     'remote_processing_allowed': True, 'test_mode': False})
    assert response.status_code == 200
    assert marker not in response.text
    assert core.database.event_count() == before
    stored = core.cognition.store.list_messages(limit=24)
    assert all(marker not in message.model_dump_json() for message in stored)
    assert marker not in core.cognition.compiler.compile(request('Oi')).model_dump_json()


@pytest.mark.parametrize('text', [
    'Lia perguntou a Rui qual seu nome, e ele respondeu Carlos. Achei estranho.',
    'Lia perguntou se Rui gosta de mulheres, e ele disse que sim. Foi uma conversa entre eles.',
    'A Lia perguntou ao Rui: "qual seu nome?" Ele respondeu Carlos.',
    'Lia perguntou para Rui: "Qual gênero tu quer escolher?" Ela queria entender o personagem dele.',
    'O Rui disse à Lia "cala a boca". Isso foi grosseiro?',
])
def test_third_party_reports_never_authorize_amora_self_choices(core, text):
    context = core.cognition.compiler.compile(request(text))
    assert context.dialogue_plan['self_choice_keys'] == []
    assert context.dialogue_plan['fact_key'] is None
    assert context.dialogue_plan['act'] != 'set_boundary'


@pytest.mark.parametrize('text', [
    'Amora, eu te perguntei: "qual seu nome?"',
    'Amora, responde isto pra mim: "qual seu nome?"',
    '"Qual seu nome?", Amora.',
])
def test_direct_quoted_request_still_has_canonical_answer(core, text):
    context = core.cognition.compiler.compile(request(text))
    assert context.dialogue_plan['fact_key'] == 'canonical_name'


def test_legitimate_self_choice_survives_evidence_gate(core):
    original = request()
    context = core.cognition.compiler.compile(original)
    assert 'gender_identity' in context.dialogue_plan['self_choice_keys']
    frame = RealizationValidator.parse_and_validate(context, {
        'moves': [{'kind': 'answer_question', 'text': 'Eu me identifico como mulher.'}],
        'closing': 'statement',
        'self_choices': [{'key': 'gender_identity', 'value': 'mulher'}],
    })
    decision = commit_self_choices(self_model=core.self_model, request=original,
                                   context=context, choices=frame.frame.self_choices)
    assert decision.committed_keys == ('gender_identity',)
    assert core.self_model.get(SelfLayer.TRAITS, 'gender_identity').value == 'mulher'


@pytest.mark.parametrize('tamper', ['request_turn', 'request_text', 'request_target', 'request_speaker',
                                   'request_remote', 'request_auth', 'request_secret',
                                   'context_turn', 'context_text', 'forged_plan'])
def test_stale_or_forged_self_choice_cannot_write_identity(core, tamper):
    original = request()
    context = core.cognition.compiler.compile(original)
    assert 'gender_identity' in context.dialogue_plan['self_choice_keys']
    if tamper == 'request_turn':
        original = request()
    elif tamper == 'request_text':
        original = original.model_copy(update={'text': 'Lia perguntou a Rui sobre o gênero dele.'})
    elif tamper == 'request_target':
        original = original.model_copy(update={'target_id': 'PERSON_RUI'})
    elif tamper == 'request_speaker':
        original = original.model_copy(update={'speaker_id': 'PERSON_RUI'})
    elif tamper == 'request_remote':
        original = original.model_copy(update={'remote_processing_allowed': False})
    elif tamper in {'request_auth', 'request_secret'}:
        original = original.model_copy(update={'privacy_scope': PrivacyScope.AUTH if tamper.endswith('auth') else PrivacyScope.SECRET})
    elif tamper == 'context_turn':
        context = context.model_copy(update={'turn_id': request().turn_id})
    elif tamper == 'context_text':
        context = context.model_copy(update={'user_text': 'Qual foi o placar do jogo?'})
    elif tamper == 'forged_plan':
        context = core.cognition.compiler.compile(request('Lia perguntou a Rui sobre o gênero dele.'))
        original = request('Lia perguntou a Rui sobre o gênero dele.', turn_id=context.turn_id)
        context.dialogue_plan['self_choice_keys'] = ['gender_identity']
        context.dialogue_plan.pop('self_choice_evidence', None)
    result = commit_self_choices(self_model=core.self_model, request=original,
                                  context=context, choices=[choice()])
    assert result.committed_keys == ()
    assert core.self_model.get(SelfLayer.TRAITS, 'gender_identity') is None


@pytest.mark.parametrize('scope', [PrivacyScope.AUTH, PrivacyScope.SECRET])
async def test_messaging_sensitive_envelope_stops_before_attention_audit_and_cognition(core, scope, monkeypatch):
    from amora.messaging import ChannelType, ExternalActorRef, InboundMessage, MessagingPlatform, RoutingAction
    marker = 'SYNTHETIC_MESSAGING_BOUNDARY_' + scope.value
    def no_attention(*args, **kwargs):
        raise AssertionError('protected content reached attention router')
    monkeypatch.setattr(core.messaging.attention, 'route', no_attention)
    incoming = InboundMessage(
        platform=MessagingPlatform.DISCORD, account_scope='primary',
        conversation_id='adversarial-protected-channel', message_id='protected-' + scope.value,
        channel_type=ChannelType.GROUP,
        sender_identifiers=[ExternalActorRef(identifier_type='discord_user_id',
                                              external_actor_id='111111111111111111', confidence=1.0)],
        text=marker, mentions_amora=True, timestamp=core.clock.now(),
        privacy_scope=scope, remote_processing_allowed=True)
    before_audit = core.messaging.store.count_audit()
    result = await core.messaging.receive(incoming)
    assert result.decision.action == RoutingAction.BLOCK
    assert marker not in result.model_dump_json()
    assert core.messaging.store.count_audit() == before_audit
    assert core.cognition.store.message_count() == 0
    assert core.messaging.store.get_audit(platform=incoming.platform, account_scope=incoming.account_scope,
                                         conversation_id=incoming.conversation_id, message_id=incoming.message_id) is None


@pytest.mark.parametrize('text', [
    'Eu gosto de mulheres adultas e escolhi meu gênero. Só estou te contando.',
    'Eu gosto de mulheres, como você já sabe.',
    'Eu gosto do teu gênero.',
    'Lia perguntou a Rui se Amora gosta de mulheres e qual seria sua preferência.',
    'Eu preferia um corpo musculoso pra mim, não estou perguntando sobre você.',
    'Lia perguntou à Amora: "Qual gênero tu quer escolher?" Eu só estou narrando a conversa.',
])
def test_speaker_disclosure_and_report_to_amora_do_not_authorize_choice(core, text):
    context = core.cognition.compiler.compile(request(text))
    assert context.dialogue_plan['self_choice_keys'] == []


@pytest.mark.parametrize('tamper', ['evidence_missing', 'context_auth', 'context_secret', 'context_remote'])
def test_self_choice_evidence_and_context_permissions_rechecked(core, tamper):
    original = request()
    context = core.cognition.compiler.compile(original)
    assert 'gender_identity' in context.dialogue_plan['self_choice_keys']
    if tamper == 'evidence_missing':
        context.dialogue_plan.pop('self_choice_evidence', None)
    elif tamper == 'context_auth':
        context = context.model_copy(update={'privacy_scope': PrivacyScope.AUTH})
    elif tamper == 'context_secret':
        context = context.model_copy(update={'privacy_scope': PrivacyScope.SECRET})
    elif tamper == 'context_remote':
        context = context.model_copy(update={'remote_processing_allowed': False})
    decision = commit_self_choices(self_model=core.self_model, request=original,
                                    context=context, choices=[choice()])
    assert decision.committed_keys == ()
    assert core.self_model.get(SelfLayer.TRAITS, 'gender_identity') is None


def test_direct_choice_after_closed_report_is_not_erased(core):
    context = core.cognition.compiler.compile(request('Lia disse: "Oi." Qual gênero tu quer escolher?'))
    assert 'gender_identity' in context.dialogue_plan['self_choice_keys']


@pytest.mark.parametrize('scope', [PrivacyScope.AUTH, PrivacyScope.SECRET])
def test_audio_sensitive_scope_is_blocked_before_cache_or_stt(core, scope):
    class ForbiddenStt:
        calls = 0
        async def transcribe(self, segment):
            self.calls += 1
            raise AssertionError('protected audio reached STT')
    fake = ForbiddenStt()
    core.stt = fake
    incoming = core.paths.cache / 'incoming'
    assert not incoming.exists()
    before_events = core.database.event_count()
    headers = {'Authorization': 'Bearer ' + core.secrets.get('LOCAL_API_TOKEN'), 'Content-Type': 'audio/wav'}
    with TestClient(create_api(core)) as client:
        response = client.post('/v1/audio/transcribe?privacy_scope=' + scope.value,
                               headers=headers, content=b'RIFFSYNTHETIC_PROTECTED_AUDIO')
    assert response.status_code in {400, 403, 422}
    assert fake.calls == 0
    assert not incoming.exists()
    assert core.database.event_count() == before_events
