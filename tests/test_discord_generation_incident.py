"""Offline incident reproductions: no credentials, sockets or real provider calls."""
import asyncio
from pathlib import Path
import runpy

import pytest
from amora.app import AmoraCore
from amora.cognition.providers import ProviderError

# Reuse the established transport fixture without making tests a Python package.
_helpers = runpy.run_path(str(Path(__file__).with_name('test_messaging_lifecycle_stability.py')))
CountingProvider = _helpers['CountingStructuredProvider']
message = _helpers['_discord_direct_message']
clock = _helpers['_clock']


@pytest.fixture
def core(tmp_path, monkeypatch):
    monkeypatch.setenv('AMORA_HOME', str(tmp_path / 'runtime'))
    for name in ('NVIDIA_API_KEY', 'GROQ_API_KEY', 'GEMINI_API_KEY', 'OPENROUTER_API_KEY'):
        monkeypatch.delenv('AMORA_SECRET_' + name, raising=False)
    core = AmoraCore.bootstrap(clock=clock())
    yield core
    core.close()


@pytest.mark.asyncio
@pytest.mark.parametrize('remote', [False, True])
async def test_unavailable_remote_is_operational_failure_not_social_ack(core, remote):
    incoming = message(core).model_copy(update={'remote_processing_allowed': remote,
        'text': 'Sei mas é serio amora vc ta recebendo tudo dboa'})
    result = await core.messaging.receive(incoming)
    assert len(result.outbound) == 1
    outbound = result.outbound[0]
    assert outbound.metadata["generation_latency_ms"] >= 0
    assert outbound.metadata["generation_stage"] == ("fallback" if remote else "local")
    assert (outbound.metadata["generation_error_code"] is not None) == remote
    if remote:
        assert outbound.text != 'Uhum.', 'remote failure was disguised as conversational agreement'
        assert '[AMORA:E_' in outbound.text
        assert outbound.metadata['fallback_used'] is True
        assert 'NO_ELIGIBLE_STRUCTURED_PROVIDER' in outbound.metadata['cognitive_reason_codes']
    else:
        assert outbound.text == 'Uhum.'


class FaultProvider(CountingProvider):
    def __init__(self, fault):
        super().__init__(name='offline-nvidia', delay=0)
        self.fault = fault

    async def realize(self, context):
        self.calls += 1
        if self.fault == 'slow':
            await asyncio.sleep(10)
        if self.fault == 'invalid-json':
            return {'moves': 'invalid'}
        raise ProviderError('synthetic remote failure', status_code=int(self.fault))


@pytest.mark.asyncio
@pytest.mark.parametrize('fault', ['slow', 'invalid-json', '429', '500'])
async def test_remote_fault_is_observable_and_not_uhum(core, fault):
    provider = FaultProvider(fault)
    core.cognition.register_provider(provider)
    core.messaging.provider_total_budget_seconds = .05
    core.messaging.provider_attempt_budget_seconds = .02
    result = await core.messaging.receive(message(core, message_id='fault-' + fault))
    assert provider.calls == 1
    assert len(result.outbound) == 1
    outbound = result.outbound[0]
    assert outbound.metadata['fallback_used'] is True
    assert outbound.text != 'Uhum.'
    assert '[AMORA:E_' in outbound.text
    assert outbound.metadata['cognitive_reason_codes']


@pytest.mark.asyncio
async def test_http_client_cancel_does_not_duplicate_or_cancel_logical_turn(core):
    provider = CountingProvider(delay=.05)
    core.cognition.register_provider(provider)
    incoming = message(core, message_id='cancel-and-retry')
    waiter = asyncio.create_task(core.messaging.receive(incoming))
    await asyncio.sleep(.01)
    waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiter
    result = await core.messaging.receive(incoming)
    assert provider.calls == 1
    assert len(result.outbound) == 1
    again = await core.messaging.receive(incoming)
    assert provider.calls == 1
    assert again.duplicate is True
    assert [x.id for x in result.outbound] == [x.id for x in again.outbound]


@pytest.mark.asyncio
async def test_quota_backoff_skips_provider_and_preserves_failure_identity(core):
    provider = FaultProvider('429')
    core.cognition.register_provider(provider)
    first = await core.messaging.receive(message(core, message_id='quota-first'))
    initial_calls = provider.calls
    second = await core.messaging.receive(message(core, message_id='quota-second'))
    assert initial_calls >= 1
    assert provider.calls == initial_calls
    assert first.outbound[0].metadata['fallback_used'] is True
    assert 'PROVIDERS_IN_BACKOFF' in second.outbound[0].metadata['cognitive_reason_codes']
    assert second.outbound[0].text != 'Uhum.'


@pytest.mark.asyncio
async def test_restart_replays_completed_outbound_without_regeneration(core):
    provider = CountingProvider()
    core.cognition.register_provider(provider)
    incoming = message(core, message_id='completed-before-restart')
    first = await core.messaging.receive(incoming)
    # Replace the messaging process-local inflight map/service, keep durable stores.
    from amora.messaging.service import PlatformMessagingService
    replacement = PlatformMessagingService(store=core.messaging.store,
        attention=core.messaging.attention, cognition=core.cognition,
        conversation=core.conversation, clock=core.clock,
        presence_control=core.messaging.presence_control)
    replay = await replacement.receive(incoming)
    assert replay.duplicate is True
    assert provider.calls == 1
    assert [item.id for item in first.outbound] == [item.id for item in replay.outbound]


class GatedProvider(CountingProvider):
    def __init__(self):
        super().__init__()
        self.entered = asyncio.Event()
        self.release = asyncio.Event()

    async def realize(self, context):
        self.entered.set()
        await self.release.wait()
        return await super().realize(context)


def replacement_service(core):
    from amora.messaging.service import PlatformMessagingService
    return PlatformMessagingService(store=core.messaging.store,
        attention=core.messaging.attention, cognition=core.cognition,
        conversation=core.conversation, clock=core.clock,
        presence_control=core.messaging.presence_control)


@pytest.mark.asyncio
@pytest.mark.parametrize('reason', ['DISCORD_MESSAGE_UPDATED', 'DISCORD_MESSAGE_DELETED'])
async def test_edit_delete_during_generation_suppresses_stale_delivery(core, reason):
    from amora.messaging.models import TransportInvalidation
    provider = GatedProvider()
    core.cognition.register_provider(provider)
    incoming = message(core, message_id='invalidate-' + reason)
    pending = asyncio.create_task(core.messaging.receive(incoming))
    await asyncio.wait_for(provider.entered.wait(), 2)
    core.messaging.invalidate_transport(TransportInvalidation(
        platform=incoming.platform, account_scope=incoming.account_scope,
        conversation_id=incoming.conversation_id,
        native_message_id=incoming.native_source_message_id,
        reason=reason, transport_revision=2))
    provider.release.set()
    result = await asyncio.wait_for(pending, 2)
    assert result.outbound == []
    assert 'REPLY_LEASE_INVALIDATED' in result.decision.reason_codes
    retry = await core.messaging.receive(incoming)
    assert retry.outbound == []


@pytest.mark.asyncio
async def test_two_service_instances_share_durable_single_generation(core):
    provider = GatedProvider()
    core.cognition.register_provider(provider)
    incoming = message(core, message_id='two-processes')
    other = replacement_service(core)
    first = asyncio.create_task(core.messaging.receive(incoming))
    await asyncio.wait_for(provider.entered.wait(), 2)
    second = asyncio.create_task(other.receive(incoming))
    await asyncio.sleep(.02)
    provider.release.set()
    outcomes = await asyncio.wait_for(asyncio.gather(first, second), 2)
    assert provider.calls == 1, 'process-local inflight maps do not share generation ownership'
    assert all(len(result.outbound) == 1 for result in outcomes)
    assert outcomes[0].outbound[0].id == outcomes[1].outbound[0].id


@pytest.mark.asyncio
async def test_enqueue_database_failure_can_retry_without_regeneration(core, monkeypatch):
    import sqlite3
    provider = CountingProvider()
    core.cognition.register_provider(provider)
    incoming = message(core, message_id='db-enqueue-failure')
    original = core.messaging.store.put_outbound
    def fail_once(outbound, **kwargs):
        monkeypatch.setattr(core.messaging.store, 'put_outbound', original)
        raise sqlite3.OperationalError('synthetic enqueue failure')
    monkeypatch.setattr(core.messaging.store, 'put_outbound', fail_once)
    with pytest.raises(sqlite3.OperationalError, match='synthetic enqueue failure'):
        await core.messaging.receive(incoming)
    recovered = await core.messaging.receive(incoming)
    assert len(recovered.outbound) == 1
    assert provider.calls == 1


@pytest.mark.asyncio
async def test_provider_total_budget_excludes_context_compilation(core, monkeypatch):
    from amora.cognition import CognitiveRequest
    import amora.cognition.structured_runtime as runtime
    provider = CountingProvider()
    core.cognition.register_provider(provider)
    elapsed = [100.0]
    original = core.cognition._prepare_context
    def costly_compile(request):
        context = original(request)
        elapsed[0] += 2.0
        return context
    monkeypatch.setattr(runtime, 'perf_counter', lambda: elapsed[0])
    monkeypatch.setattr(core.cognition, '_prepare_context', costly_compile)
    plan = await core.cognition.respond(CognitiveRequest(
        text='Me conta algo engraçado sobre programação.', speaker_id='PERSON_BK',
        target_id='ENTITY_AMORA', remote_processing_allowed=True,
        metadata={'test_mode': True, 'provider_total_budget_seconds': 1.,
                  'provider_attempt_budget_seconds': .5}), fallback_text='operational')
    assert provider.calls == 1
    assert plan.provider == provider.descriptor.name
    assert not plan.fallback_used


@pytest.mark.asyncio
async def test_remote_error_body_is_not_written_to_operator_logs_or_journal(core, caplog):
    import logging
    caplog.set_level(logging.INFO, logger="amora.cognition.generation")
    marker = 'SYNTHETIC_SECRET_BODY_NEVER_LOG'
    class UnsafeErrorProvider(CountingProvider):
        async def realize(self, context):
            raise ProviderError(marker, status_code=503)
    core.cognition.register_provider(UnsafeErrorProvider())
    result = await core.messaging.receive(message(core, message_id='safe-diagnostics'))
    assert result.outbound[0].metadata['fallback_used'] is True
    journal = core.cognition.store.recent_journal(limit=100)
    assert marker not in caplog.text
    assert marker not in str([entry.model_dump() for entry in journal])
    assert marker not in result.model_dump_json()

    records = [r for r in caplog.records if r.name == "amora.cognition.generation"]
    selected = [r for r in records if r.stage == "provider_selected"]
    errors = [r for r in records if getattr(r, "status_code", None) == 503]
    assert selected and errors
    assert selected[0].turn_id == errors[0].turn_id
    assert errors[0].latency_ms >= 0
    assert errors[0].provider is not None
    assert not hasattr(errors[0], "payload")
