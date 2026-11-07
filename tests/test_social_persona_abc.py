import base64
import json
from types import SimpleNamespace

import httpx
import pytest

from amora.cognition.mistral_prompt_experiment import (
    SOCIAL_CASES, load_archive, run_mistral_abc, social_context,
)
from amora.cognition.providers import ProviderError
from amora.cognition.providers_nvidia import NvidiaMistralLarge4ProductionProvider
from amora.cognition.providers_openrouter import ExperimentalVeniceSocialProvider


@pytest.mark.parametrize('case', SOCIAL_CASES)
async def test_three_calls_frozen_context_blind_full_outputs(case, monkeypatch):
    calls = []
    context = social_context(case)
    kind = context.dialogue_plan['response_moves'][0]
    raw = json.dumps({'moves': [{'kind': kind, 'text': 'Oxente, essa foi boa.'}], 'closing': 'statement'})

    async def fake(self, payload, **kwargs):
        calls.append(payload)
        return {'choices': [{'message': {'content': raw}}], 'usage': {'prompt_tokens': 20, 'completion_tokens': 12}}, {}, 200

    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, '_post_chat', fake)
    monkeypatch.setattr(ExperimentalVeniceSocialProvider, 'post_chat', fake)
    secrets = SimpleNamespace(get=lambda name: 'secret-never-report')
    progress = []
    result = await run_mistral_abc(SimpleNamespace(secrets=secrets), case=case, social=True, on_result=progress.append)
    assert len(calls) == 3
    assert all(item['validated'] for item in result['variants'])
    assert all(item['candidate_output'] == raw for item in progress)
    assert all('variant' not in item for item in progress)
    assert 'secret-never-report' not in json.dumps(result)
    normalized = [p['messages'][1:] if p['messages'][0]['content'] == load_archive() else p['messages'] for p in calls]
    assert normalized[0] == normalized[1] == normalized[2]
    assert len(json.loads(base64.b64decode(result['reveal_token']))) == 3
    assert result['context_sha256']
    assert social_context(case) == context
    assert len(result['rubric']) == 9


@pytest.mark.parametrize('raw,code', [('not JSON', 'EXPERIMENT_JSON_INVALID'),
    ('{"moves":[{"kind":"invented","text":"Oi"}]}', 'UNAUTHORIZED_REALIZATION_MOVE')])
async def test_failures_never_bypass_validator(raw, code, monkeypatch):
    async def fake(self, payload, **kwargs):
        return {'choices': [{'message': {'content': raw}}]}, {}, 200
    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, '_post_chat', fake)
    monkeypatch.setattr(ExperimentalVeniceSocialProvider, 'post_chat', fake)
    result = await run_mistral_abc(SimpleNamespace(secrets=SimpleNamespace(get=lambda _: 'secret')), case='bug', social=True)
    assert all(not x['validated'] and x['error_code'] == code and 'text' not in x for x in result['variants'])


async def test_429_stops_without_retries(monkeypatch):
    calls = []
    async def fake(self, payload, **kwargs):
        calls.append(payload)
        raise ProviderError('never serialize secret', status_code=429, retry_after='60')
    monkeypatch.setattr(NvidiaMistralLarge4ProductionProvider, '_post_chat', fake)
    monkeypatch.setattr(ExperimentalVeniceSocialProvider, 'post_chat', fake)
    report = await run_mistral_abc(SimpleNamespace(secrets=SimpleNamespace(get=lambda _: 'secret')), case='bug', social=True)
    assert len(calls) == 1
    assert report['variants'][0]['error_code'] == 'EXPERIMENT_RATE_LIMITED'
    assert 'never serialize secret' not in json.dumps(report)


async def test_venice_http_transport(monkeypatch):
    requests = []
    def respond(request):
        requests.append(request)
        assert request.url.path == '/api/v1/chat/completions'
        payload = json.loads(request.content)
        assert payload['model'].endswith(':free')
        assert payload['provider']['allow_fallbacks'] is False
        assert 'response_format' not in payload
        assert request.headers['authorization'] == 'Bearer store-only'
        return httpx.Response(200, json={'choices': [{'message': {'content': '{}'}}]})
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    adapter = ExperimentalVeniceSocialProvider(SimpleNamespace(get=lambda _: 'store-only'))
    await adapter.post_chat(adapter.payload({'messages': [], 'temperature': .5, 'top_p': 1, 'max_tokens': 4096}))
    assert len(requests) == 1


async def test_venice_transport_429_one_http_request(monkeypatch):
    requests = []
    def respond(request):
        requests.append(request)
        return httpx.Response(429, headers={'retry-after': '120'}, text='credential must not leak')
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kw: original(transport=httpx.MockTransport(respond), **kw))
    with pytest.raises(ProviderError) as error:
        await ExperimentalVeniceSocialProvider(SimpleNamespace(get=lambda _: 'key')).post_chat({})
    assert error.value.status_code == 429
    assert error.value.retry_after == '120'
    assert 'credential' not in str(error.value)
    assert len(requests) == 1


async def test_shell_missing_secret_and_closed_arguments():
    import asyncio
    from amora.admin_commands import AmoraCommandBus
    bus = AmoraCommandBus()
    for command, expected in [
        ('providers experiment social-persona-abc --case bug', 'EXPERIMENT_OPENROUTER_SECRET_MISSING'),
        ('providers experiment social-persona-abc --case /etc/passwd', 'COMMAND_ARGUMENT_INVALID'),
        ('providers experiment social-persona-abc --prompt override', 'COMMAND_ARGUMENT_INVALID'),
    ]:
        core = SimpleNamespace(secrets=SimpleNamespace(get=lambda name: 'key' if name == 'NVIDIA_API_KEY' else None))
        started = bus.start(core, command)
        for _ in range(100):
            job = bus.get(started.job_id)
            if job.status in {'failed', 'succeeded'}:
                break
            await asyncio.sleep(.01)
        assert job.error_code == expected
        assert not job.progress
