from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import httpx

from .providers import ProviderError


_NON_RETRYABLE_STATUS = {400, 401, 403, 404, 405, 409, 422}


@dataclass(frozen=True, slots=True)
class ProviderFailure:
    code: str
    retryable: bool
    status_code: int | None = None


def classify_provider_failure(exc: BaseException) -> ProviderFailure:
    """Classify transport/provider failures without leaking provider payloads."""
    if isinstance(exc, asyncio.TimeoutError | httpx.TimeoutException):
        return ProviderFailure("PROVIDER_TIMEOUT", True)
    if isinstance(exc, httpx.NetworkError):
        return ProviderFailure("PROVIDER_NETWORK", True)
    if isinstance(exc, ProviderError):
        status = exc.status_code
        if status in _NON_RETRYABLE_STATUS:
            return ProviderFailure(f"PROVIDER_HTTP_{status}", False, status)
        if status == 429:
            return ProviderFailure("PROVIDER_RATE_LIMIT", True, status)
        if status is not None and 500 <= status <= 599:
            return ProviderFailure("PROVIDER_UPSTREAM_5XX", True, status)
        if status is None:
            return ProviderFailure("PROVIDER_TRANSIENT", True)
        return ProviderFailure(f"PROVIDER_HTTP_{status}", False, status)
    return ProviderFailure(type(exc).__name__, False)


class ResilientProviderProxy:
    """Bounded retry proxy for structured realization.

    The exact same frozen CognitiveContext is reused for every attempt. The outer
    cognition runtime still owns the turn/message id, total attempt budget,
    provider failover, cooldown/circuit-breaker state and final fallback.
    """

    def __init__(self, provider: Any, *, max_retries: int = 1, base_delay_seconds: float = 0.08) -> None:
        self._provider = provider
        self.descriptor = provider.descriptor
        self.max_retries = max(0, int(max_retries))
        self.base_delay_seconds = max(0.0, float(base_delay_seconds))

    def __getattr__(self, name: str) -> Any:
        return getattr(self._provider, name)

    async def realize(self, context):
        realize = getattr(self._provider, "realize", None)
        if not callable(realize):
            raise AttributeError("wrapped provider has no realize interface")

        attempt = 0
        while True:
            try:
                return await realize(context)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                failure = classify_provider_failure(exc)
                if not failure.retryable or attempt >= self.max_retries:
                    raise
                attempt += 1
                # Short deterministic backoff. The outer per-turn wait_for is the
                # hard deadline, so this can never extend a surface budget.
                delay = self.base_delay_seconds * (2 ** (attempt - 1))
                if delay:
                    await asyncio.sleep(delay)


def wrap_resilient_provider(provider: Any) -> Any:
    if isinstance(provider, ResilientProviderProxy):
        return provider
    if not callable(getattr(provider, "realize", None)):
        return provider
    return ResilientProviderProxy(provider)
