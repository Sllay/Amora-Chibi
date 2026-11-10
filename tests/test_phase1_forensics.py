"""Phase 1 reproductions retained as passing Phase 2 regressions.

F04/F05 accept explicit irreducible overflow rather than silently losing
essential context. No remote generations or original prompt edits.
"""
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID

import pytest

from amora.cognition.context import ContextCompiler
from amora.cognition.dialogue import DialogueOrchestrator
from amora.cognition.models import CognitiveContext, CognitiveRequest
from amora.cognition.realization import RealizationValidator
from amora.cognition.providers_nvidia import NvidiaMistralLarge4ProductionProvider, NvidiaStreamTimeout
from amora.cognition.amora_v2_v3_experiment import load_v2, load_v3, paired_payloads


def context(text, **kwargs):
    return CognitiveContext(turn_id=UUID(int=700), created_at=datetime(2026, 11, 8, tzinfo=timezone.utc),
                            speaker_id='PERSON_BK', target_id='ENTITY_AMORA', user_text=text,
                            self_model={'core_identity': {'canonical_name': 'Amora'}}, **kwargs)


def test_indirect_question_does_not_request_amora_name():
    ctx = context('Lia perguntou a Rui qual seu nome, e ele respondeu Carlos. Achei estranho.')
    plan = DialogueOrchestrator().plan(ctx)
    assert plan.fact_key is None, plan.model_dump()


def test_indirect_attraction_does_not_authorize_self_choice():
    ctx = context('Lia perguntou se Rui gosta de mulheres, e ele disse que sim. Foi uma conversa entre eles.')
    plan = DialogueOrchestrator().plan(ctx)
    assert plan.self_choice_keys == [], plan.model_dump()


def test_quoted_direct_question_preserves_request():
    ctx = context('Amora, eu te perguntei: "qual seu nome?"')
    plan = DialogueOrchestrator().plan(ctx)
    assert plan.fact_key == 'canonical_name', plan.model_dump()


def test_budget_is_respected_when_irreducible_input_exceeds_it():
    from amora.cognition.context_budget import ContextBudgetExceeded
    with pytest.raises(ContextBudgetExceeded) as error:
        ContextCompiler._fit_budget(context('x' * 2500, token_budget=256))
    assert error.value.estimated > error.value.budget



def test_trimmed_snapshot_has_consistent_plan():
    ctx = context('Tu entendeu?', token_budget=256, recent_messages=[
        {'speaker_id': 'PERSON_BK', 'text': 'Meu desenho foi ignorado.'},
        {'speaker_id': 'ENTITY_AMORA', 'text': 'Tu queria que reparassem no trabalho.'},
    ])
    ctx.dialogue_plan = DialogueOrchestrator().plan(ctx).model_dump(mode='json')
    assert ctx.dialogue_plan['allow_reflective_question']
    from amora.cognition.context_budget import ContextBudgetExceeded
    try:
        ContextCompiler._fit_budget(ctx)
    except ContextBudgetExceeded:
        assert len(ctx.recent_messages) == 2  # preserve causal pair; explicit overflow
    assert ctx.dialogue_plan['allow_reflective_question'] == DialogueOrchestrator().plan(ctx).allow_reflective_question


@pytest.mark.parametrize('text', ['Oxente, essa porra foi boa.', 'Tu e esse compilador tão de sacanagem.',
                                   'Bora baixar o tom, que eu não sou saco de pancada.'])
def test_structural_validator_accepts_colloquial_vocabulary(text):
    ctx = context('Me zoa na brincadeira kkkkk')
    ctx.dialogue_plan = DialogueOrchestrator().plan(ctx).model_dump(mode='json')
    result = RealizationValidator.parse_and_validate(ctx, {
        'moves': [{'kind': ctx.dialogue_plan['response_moves'][0], 'text': text}], 'closing': 'statement'})
    assert result.text == text


def test_production_payload_has_no_v3_but_lab_explicitly_injects_it():
    provider = NvidiaMistralLarge4ProductionProvider(SimpleNamespace(get=lambda _: None))
    ctx = context('Como tu tá?')
    ctx.dialogue_plan = DialogueOrchestrator().plan(ctx).model_dump(mode='json')
    v3 = load_v3()
    production = provider._payload(ctx)
    lab = paired_payloads(provider, ctx, load_v2(), v3)
    assert len(production['messages']) == 2
    assert all(message['content'] != v3 for message in production['messages'])
    assert lab['B_virtual_human_v3']['messages'][0]['content'] == v3


async def test_provider_idle_timeout_is_not_a_surface_budget_timeout(tmp_path, monkeypatch):
    from amora.app import AmoraCore
    monkeypatch.setenv('AMORA_HOME', str(tmp_path / 'runtime'))
    monkeypatch.setenv('AMORA_SECRET_NVIDIA_API_KEY', 'synthetic-test-key')
    core = AmoraCore.bootstrap()
    async def timeout(*args, **kwargs):
        raise NvidiaStreamTimeout({'stage': 'waiting_first_event', 'timeout_kind': 'idle'})
    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, '_post_chat', timeout)
    request = CognitiveRequest(text='Qual tua opinião sobre desenho?', speaker_id='PERSON_BK',
                               remote_processing_allowed=True, metadata={'test_mode': True})
    try:
        response = await core.cognition.respond(request)
        assert response.fallback_used
        entries = core.cognition.store.journal_for_turn(request.turn_id)
        assert not any('SURFACE_BUDGET_TIMEOUT' in row.reason_codes for row in entries)
    finally:
        core.close()


async def test_secret_observation_has_no_durable_raw_text(tmp_path, monkeypatch):
    from amora.app import AmoraCore
    from amora.events import PrivacyScope
    monkeypatch.setenv('AMORA_HOME', str(tmp_path / 'runtime'))
    core = AmoraCore.bootstrap()
    try:
        observation = await core.language_social.observe_text(
            'SYNTHETIC_SECRET_MARKER', speaker_id='PERSON_BK', privacy_scope=PrivacyScope.SECRET,
            explicit_addressee_id='ENTITY_AMORA')
        row = core.database._conn.execute('SELECT payload_json FROM events WHERE id=?', (observation.event_id,)).fetchone()
        assert row is None or 'SYNTHETIC_SECRET_MARKER' not in row['payload_json']
    finally:
        core.close()


def test_canonical_parent_is_recognized(tmp_path, monkeypatch):
    from amora.app import AmoraCore
    from amora.cognition.realization import _speaker_is_parent
    monkeypatch.setenv('AMORA_HOME', str(tmp_path / 'runtime'))
    core = AmoraCore.bootstrap()
    try:
        compiled = core.cognition.compiler.compile(CognitiveRequest(text='Amora', speaker_id='PERSON_BK'))
        assert any(r['relation_type'] == 'father_of' for r in compiled.relationships)
        assert _speaker_is_parent(compiled)
    finally:
        core.close()


async def test_indirect_report_cannot_commit_amora_preference(tmp_path, monkeypatch):
    from amora.app import AmoraCore
    from amora.world import SelfLayer
    monkeypatch.setenv('AMORA_HOME', str(tmp_path / 'runtime'))
    monkeypatch.setenv('AMORA_SECRET_NVIDIA_API_KEY', 'synthetic-test-key')
    core = AmoraCore.bootstrap()
    async def synthetic(self, ctx):
        assert 'physical_attraction' not in ctx.dialogue_plan['self_choice_keys']
        return {'moves': [{'kind': ctx.dialogue_plan['response_moves'][0],
                           'text': 'Eu gosto de pessoas adultas com estilo.'}],
                'closing': 'statement',
                'self_choices': [{'key': 'physical_attraction', 'value': 'SYNTHETIC_PREFERENCE'}]}
    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, 'realize', synthetic)
    try:
        await core.cognition.respond(CognitiveRequest(
            text='Lia perguntou se Rui gosta de mulheres, e ele disse que sim. Foi uma conversa entre eles.',
            speaker_id='PERSON_BK', remote_processing_allowed=True,
            metadata={'memory_consolidation': False}))
        assert core.self_model.get(SelfLayer.PREFERENCES, 'physical_attraction') is None
    finally:
        core.close()


def test_web_default_test_mode_is_not_continuity_or_read_only(tmp_path, monkeypatch):
    from amora.app import AmoraCore
    from amora.api import create_api
    from fastapi.testclient import TestClient
    monkeypatch.setenv('AMORA_HOME', str(tmp_path / 'runtime'))
    core = AmoraCore.bootstrap()
    try:
        with TestClient(create_api(core)) as client:
            response = client.post('/v1/web/utterance', headers={
                'Authorization': 'Bearer ' + core.secrets.get('LOCAL_API_TOKEN')},
                json={'text': 'O gerente do caos perdeu o ponto e vírgula.', 'remote_processing_allowed': False})
            assert response.status_code == 200
            assert response.json()['test_mode'] is True
            assert core.database._conn.execute(
                "SELECT count(*) FROM events WHERE event_type='language.utterance.observed'").fetchone()[0] > 0
            compiled = core.cognition.compiler.compile(CognitiveRequest(
                text='Lembra da piada?', speaker_id='PERSON_BK', remote_processing_allowed=False))
            assert compiled.recent_messages == []
            assert 'TEST_TURNS_EXCLUDED_FROM_RECENT' in compiled.reason_codes
    finally:
        core.close()


def test_lab_bold_scene_does_not_represent_fresh_runtime_state(tmp_path, monkeypatch):
    from amora.app import AmoraCore
    from amora.cognition.amora_v2_v3_experiment import paired_context
    monkeypatch.setenv('AMORA_HOME', str(tmp_path / 'runtime'))
    core = AmoraCore.bootstrap()
    try:
        lab = paired_context('sacanagem')
        real = core.cognition.compiler.compile(CognitiveRequest(
            text=lab.user_text, speaker_id='PERSON_BK', remote_processing_allowed=True,
            metadata={'test_mode': True}))
        assert lab.drives['play'] == 0.85
        assert lab.dialogue_plan['act'] == 'tease'
        assert 'bold_tease' in lab.dialogue_plan['response_moves']
        assert real.drives['play'] == 0.22
        assert real.dialogue_plan['act'] == 'continue_topic'
        assert real.dialogue_plan['response_moves'] == ['react', 'advance_topic']
    finally:
        core.close()


async def test_generic_replay_supports_registered_structured_mistral(tmp_path, monkeypatch):
    from amora.app import AmoraCore
    monkeypatch.setenv('AMORA_HOME', str(tmp_path / 'runtime'))
    monkeypatch.setenv('AMORA_SECRET_NVIDIA_API_KEY', 'synthetic-test-key')
    monkeypatch.setenv('AMORA_SOCIAL_PRIMARY', 'mistral')
    core = AmoraCore.bootstrap()
    async def synthetic(self, ctx):
        return {'moves': [{'kind': ctx.dialogue_plan['response_moves'][0], 'text': 'Essa ideia faz sentido.'}],
                'closing': 'statement'}
    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, 'realize', synthetic)
    try:
        request = CognitiveRequest(text='Qual tua opinião sobre desenho?', speaker_id='PERSON_BK',
                                   remote_processing_allowed=True, metadata={'test_mode': True})
        plan = await core.cognition.respond(request)
        assert plan.provider == 'nvidia_mistral_large_4'
        results = await core.cognition.replay(request.turn_id, provider_names=['nvidia_mistral_large_4'])
        assert results[0].success, results[0].model_dump()
    finally:
        core.close()


def test_compiled_literal_stays_outside_trusted_system_protocol(tmp_path, monkeypatch):
    from amora.app import AmoraCore
    from amora.cognition.prompt_frame import render_trusted_protocol
    monkeypatch.setenv('AMORA_HOME', str(tmp_path / 'runtime'))
    core = AmoraCore.bootstrap()
    try:
        marker = 'SYNTHETIC_LITERAL_ONLY_MARKER'
        ctx = core.cognition.compiler.compile(CognitiveRequest(
            text=marker, speaker_id='PERSON_BK', remote_processing_allowed=True))
        assert marker not in render_trusted_protocol(ctx)
    finally:
        core.close()
