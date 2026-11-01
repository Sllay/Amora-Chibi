from __future__ import annotations

import asyncio

import httpx
import pytest

from amora.cognition.provider_resilience import (
    ResilientProviderProxy,
    classify_provider_failure,
)
from amora.cognition.providers import ProviderError


class _Descriptor:
    name = "fake"


class SequenceProvider:
    descriptor = _Descriptor()

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.context_ids = []

    async def realize(self, context):
        self.calls += 1
        self.context_ids.append(id(context))
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def test_classifies_transient_and_auth_failures() -> None:
    assert classify_provider_failure(ProviderError("rate", status_code=429)).retryable is True
    assert classify_provider_failure(ProviderError("upstream", status_code=503)).retryable is True
    assert classify_provider_failure(httpx.ConnectError("network")).retryable is True
    assert classify_provider_failure(asyncio.TimeoutError()).retryable is True
    assert classify_provider_failure(ProviderError("auth", status_code=401)).retryable is False
    assert classify_provider_failure(ProviderError("forbidden", status_code=403)).retryable is False
    assert classify_provider_failure(ProviderError("bad request", status_code=400)).retryable is False


@pytest.mark.asyncio
async def test_transient_failure_retries_once_with_exact_same_context() -> None:
    expected = {"moves": [{"kind": "answer", "text": "ok"}]}
    provider = SequenceProvider([ProviderError("upstream", status_code=503), expected])
    proxy = ResilientProviderProxy(provider, max_retries=1, base_delay_seconds=0)
    context = object()

    result = await proxy.realize(context)

    assert result == expected
    assert provider.calls == 2
    assert provider.context_ids == [id(context), id(context)]


@pytest.mark.asyncio
async def test_auth_failure_never_retries() -> None:
    provider = SequenceProvider([ProviderError("auth", status_code=401)])
    proxy = ResilientProviderProxy(provider, max_retries=3, base_delay_seconds=0)

    with pytest.raises(ProviderError):
        await proxy.realize(object())

    assert provider.calls == 1


@pytest.mark.asyncio
async def test_retry_is_bounded_and_preserves_original_failure() -> None:
    provider = SequenceProvider([
        ProviderError("one", status_code=429),
        ProviderError("two", status_code=503),
    ])
    proxy = ResilientProviderProxy(provider, max_retries=1, base_delay_seconds=0)

    with pytest.raises(ProviderError) as captured:
        await proxy.realize(object())

    assert captured.value.status_code == 503
    assert provider.calls == 2


@pytest.mark.asyncio
async def test_cancellation_is_never_retried_or_swallowed() -> None:
    provider = SequenceProvider([asyncio.CancelledError()])
    proxy = ResilientProviderProxy(provider, max_retries=3, base_delay_seconds=0)

    with pytest.raises(asyncio.CancelledError):
        await proxy.realize(object())

    assert provider.calls == 1
