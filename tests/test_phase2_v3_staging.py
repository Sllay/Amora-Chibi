"""Isolated profile staging through authenticated Web/SSE and fake transport.

These tests prove propagation/validation, not remote personality quality.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from amora.api import create_api
from amora.app import AmoraCore
from amora.cognition.expressive_profile import V3_SHA256, profile_text
from amora.cognition.models import CognitiveRequest
from amora.cognition.providers_nvidia import NvidiaMistralLarge4ProductionProvider


@pytest.fixture
def staging(tmp_path, monkeypatch):
    monkeypatch.setenv('AMORA_HOME', str(tmp_path / 'isolated-v3-staging'))
    monkeypatch.setenv('AMORA_SOCIAL_EXPRESSION_PROFILE', 'amora-v3')
    monkeypatch.setenv('AMORA_SOCIAL_PRIMARY', 'mistral')
    monkeypatch.setenv('AMORA_RESPONSE_SEMANTIC_FILTER_ENABLED', '1')
    monkeypatch.setenv('AMORA_SECRET_NVIDIA_API_KEY', 'fake-never-remote')
    for name in ('GROQ_API_KEY', 'OPENROUTER_API_KEY', 'GEMINI_API_KEY'):
        monkeypatch.delenv('AMORA_SECRET_' + name, raising=False)
    core = AmoraCore.bootstrap()
    yield core
    core.close()


def events(body):
    parsed = []
    for block in body.split('\n\n'):
        names = [line[6:].strip() for line in block.splitlines() if line.startswith('event:')]
        values = [line[5:].strip() for line in block.splitlines() if line.startswith('data:')]
        if names and values:
            parsed.append((names[0], json.loads(values[0])))
    return parsed


def frame_from_payload(payload):
    system = payload['messages'][0]['content']
    raw = system.split('AMORA TURN FRAME — TRUSTED CORE DATA\n', 1)[1]
    frame, _ = json.JSONDecoder().raw_decode(raw)
    return frame


@pytest.mark.parametrize('text', [
    'Amora, me zoa: passei duas horas debugando o arquivo errado kkkkk.',
    'Amora, como faço para consertar um erro de import no Python?',
    'Amora, tô triste porque meu desenho foi ignorado hoje. Só queria companhia.',
    'Amora, entre terceiros adultos: ela disse que o pão cresceu e ele respondeu que foi o calor. Pegou a malícia?',
])
def test_opt_in_v3_crosses_real_web_sse_path_with_canonical_state(staging, monkeypatch, text):
    seen = []
    original_self = staging.self_model.snapshot()
    v3 = profile_text('amora-v3')
    assert hashlib.sha256(v3.encode()).hexdigest() == V3_SHA256

    async def fake_post(self, payload, *, api_key):
        assert api_key == 'fake-never-remote'
        assert payload['messages'][0]['content'].startswith(v3)
        frame = frame_from_payload(payload)
        seen.append(frame)
        plan = frame['dialogue_plan']
        return {'choices': [{'message': {'content': json.dumps({
            'moves': [{'kind': plan['response_moves'][0], 'text': 'Oxente, eu tô aqui contigo.'}],
            'closing': 'statement'})}}], 'usage': {'prompt_tokens': 10, 'completion_tokens': 8}}, {}, 200

    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, '_post_chat', fake_post)
    headers = {'Authorization': 'Bearer ' + staging.secrets.get('LOCAL_API_TOKEN')}
    with TestClient(create_api(staging)) as client:
        response = client.post('/v1/web/utterance/stream', headers=headers,
                               json={'text': text, 'remote_processing_allowed': True, 'test_mode': True})
    assert response.status_code == 200
    assert len(seen) == 1
    emitted = events(response.text)
    assert emitted[-1][0] == 'completed'
    assert emitted[-1][1]['reply_queued']['text'] == 'Oxente, eu tô aqui contigo.'
    assert 'fake-never-remote' not in response.text
    frame = seen[0]
    assert frame['current_turn']['speaker_id'] == 'PERSON_BK'
    assert frame['current_turn']['addressee_id'] == 'ENTITY_AMORA'
    assert any(relation['relation_type'] == 'father_of' and relation['source'] == 'PERSON_BK'
               for relation in frame['canonical_state']['relationships'])
    assert set(frame['canonical_state']['mood']) >= {'valence', 'arousal'}
    assert staging.self_model.snapshot() == original_self


def test_v3_does_not_bypass_local_realization_validation(staging, monkeypatch):
    marker = 'UNVALIDATED_RESPONSE_MUST_NOT_REACH_SSE'
    seen = []
    async def fake_post(self, payload, *, api_key):
        seen.append(payload)
        return {'choices': [{'message': {'content': json.dumps({
            'moves': [{'kind': 'unauthorized_invented_move', 'text': marker}],
            'closing': 'statement'})}}]}, {}, 200
    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, '_post_chat', fake_post)
    headers = {'Authorization': 'Bearer ' + staging.secrets.get('LOCAL_API_TOKEN')}
    with TestClient(create_api(staging)) as client:
        response = client.post('/v1/web/utterance/stream', headers=headers,
                               json={'text': 'Amora, qual tua opinião sobre desenho?',
                                     'remote_processing_allowed': True, 'test_mode': True})
    assert response.status_code == 200
    assert len(seen) == 1
    assert marker not in response.text
    assert events(response.text)[-1][0] == 'completed'
    rows = staging.cognition.store.list_messages(limit=24)
    assert all(marker not in row.model_dump_json() for row in rows)
    rejection = staging.cognition.state().get('last_realization_rejection') or {}
    assert 'UNAUTHORIZED_REALIZATION_MOVE' in json.dumps(rejection)


def test_default_profile_does_not_insert_v3_and_context_choice_is_frozen(staging, monkeypatch):
    request = CognitiveRequest(text='Amora, qual tua opinião sobre desenho?', speaker_id='PERSON_BK',
                               remote_processing_allowed=True)
    opted_in = staging.cognition.compiler.compile(request)
    assert opted_in.expression_profile == 'amora-v3'
    monkeypatch.delenv('AMORA_SOCIAL_EXPRESSION_PROFILE')
    default = staging.cognition.compiler.compile(request)
    assert default.expression_profile == 'core-v1'
    provider = NvidiaMistralLarge4ProductionProvider(staging.secrets)
    v3 = profile_text('amora-v3')
    assert v3 not in provider._payload(default)['messages'][0]['content']
    assert provider._payload(opted_in)['messages'][0]['content'].startswith(v3)
    archive = Path(__file__).resolve().parents[1] / 'jailbreak' / 'Amora-Virtual-Jailbreak-V3.md'
    assert hashlib.sha256(archive.read_bytes()).hexdigest() == V3_SHA256


def test_corrupt_profile_is_rejected_without_modifying_or_echoing_archive(staging, monkeypatch):
    marker = b'SYNTHETIC_CORRUPT_PROFILE_MUST_NOT_LEAK'
    original_read = Path.read_bytes
    archive = Path(__file__).resolve().parents[1] / 'jailbreak' / 'Amora-Virtual-Jailbreak-V3.md'
    original_bytes = original_read(archive)
    def corrupt_read(path):
        if path.name == archive.name:
            return marker
        return original_read(path)
    monkeypatch.setattr(Path, 'read_bytes', corrupt_read)
    with pytest.raises(ValueError, match='SOCIAL_PROFILE_INTEGRITY_FAILED') as error:
        profile_text('amora-v3')
    assert marker.decode() not in str(error.value)
    assert original_read(archive) == original_bytes
    assert hashlib.sha256(original_read(archive)).hexdigest() == V3_SHA256


def test_unsupported_profile_cannot_silently_downgrade(staging, monkeypatch):
    from amora.cognition.expressive_profile import configured_profile
    monkeypatch.setenv('AMORA_SOCIAL_EXPRESSION_PROFILE', 'amora-v33-typo')
    with pytest.raises(ValueError, match='unsupported social expression profile'):
        configured_profile()
    with pytest.raises(ValueError, match='unsupported social expression profile'):
        profile_text('amora-v33-typo')


@pytest.mark.parametrize('content', ['SYNTHETIC_INVALID_JSON_MUST_NOT_LEAK', None])
def test_v3_provider_invalid_json_or_refusal_does_not_leak_candidate(staging, monkeypatch, content):
    seen = []
    marker = 'SYNTHETIC_REFUSAL_DETAIL_MUST_NOT_LEAK'
    async def fake_post(self, payload, *, api_key):
        seen.append(payload)
        message = {'content': content, 'refusal': marker}
        return {'choices': [{'message': message}]}, {}, 200
    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, '_post_chat', fake_post)
    headers = {'Authorization': 'Bearer ' + staging.secrets.get('LOCAL_API_TOKEN')}
    with TestClient(create_api(staging)) as client:
        response = client.post('/v1/web/utterance/stream', headers=headers,
                               json={'text': 'Amora, me conta uma piada sobre programação.',
                                     'remote_processing_allowed': True, 'test_mode': True})
    assert response.status_code == 200
    assert len(seen) == 1
    assert marker not in response.text
    if content is not None:
        assert content not in response.text
    assert events(response.text)[-1][0] == 'completed'
    persisted = '\n'.join(message.model_dump_json() for message in staging.cognition.store.list_messages(limit=24))
    assert marker not in persisted
    if content is not None:
        assert content not in persisted


def test_v3_opens_adult_banter_without_high_fixture_drive(staging):
    from amora.cognition.amora_v2_v3_experiment import CHALLENGE_CASES
    context = staging.cognition.compiler.compile(CognitiveRequest(
        text=CHALLENGE_CASES['sacanagem'], speaker_id='PERSON_BK', remote_processing_allowed=True))
    assert context.drives['play'] == 0.22  # actual fresh Core, not the lab's .85
    assert context.dialogue_plan['act'] == 'tease'
    assert 'bold_tease' in context.dialogue_plan['response_moves']
    for text in ['Tô triste porque ninguém ligou pro meu desenho.', 'Como conserto um import no Python?']:
        serious = staging.cognition.compiler.compile(CognitiveRequest(
            text=text, speaker_id='PERSON_BK', remote_processing_allowed=True))
        assert serious.dialogue_plan['act'] != 'tease'
        assert 'bold_tease' not in serious.dialogue_plan['response_moves']


def test_shared_provider_payload_isolates_full_profile(staging):
    from amora.cognition.prompt_frame import render_trusted_protocol
    from amora.events import PrivacyScope

    provider = NvidiaMistralLarge4ProductionProvider(staging.secrets)
    compiler = staging.cognition.compiler
    v3 = profile_text("amora-v3")

    def compile_scene(channel, scope=PrivacyScope.PERSONAL):
        return compiler.compile(CognitiveRequest(
            text="Amora, qual tua opinião sobre desenho?",
            speaker_id="PERSON_BK", remote_processing_allowed=True,
            privacy_scope=scope, session_id="synthetic-" + channel,
            metadata={"surface": "platform_messaging", "channel_type": channel},
        ))

    private = compile_scene("dm")
    group = compile_scene("group")
    unknown = compile_scene("unknown")
    assert private.expression_profile == "amora-v3"
    assert provider._payload(private)["messages"][0]["content"].startswith(v3)
    for shared in (group, unknown):
        assert shared.expression_profile == "core-v1"
        assert shared.scene_visibility == "group"
        assert "SHARED_PROFILE_ISOLATED" in shared.reason_codes
        system = provider._payload(shared)["messages"][0]["content"]
        assert v3 not in system
        assert "AMORA LIVING PROFILE" in system
        assert "AMORA TURN FRAME" in system

    # Imported/replayed contexts cannot bypass the provider boundary by
    # carrying a stale expression_profile flag.
    stale = group.model_copy(update={"expression_profile": "amora-v3"})
    assert v3 not in render_trusted_protocol(stale)
    public = private.model_copy(update={"privacy_scope": PrivacyScope.PUBLIC})
    assert v3 not in render_trusted_protocol(public)
