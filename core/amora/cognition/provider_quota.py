"""Core-owned shared provider admission. No guessed upstream RPM/TPM limits.

One bounded FIFO asyncio lock per configured account family; rate-limit
observations are shared across tasks using the same credentials. Unknown
model/account scope is conservatively treated as account-wide. No request
content, credentials, or cross-Core global state is retained.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import timedelta
import math

from .providers import ProviderError
from .provider_resilience import retry_after_seconds


def account_family(name):
    return {
        "groq": "groq", "groq_memory": "groq", "groq_vision": "groq",
        "groq_web": "groq", "nvidia_mistral_large_4": "nvidia",
        "nvidia_vision": "nvidia", "numes": "numes",
        "gemini": "gemini", "openrouter": "openrouter", "cloudflare": "cloudflare",
    }.get(name, name)


class ProviderQuotaDeferred(ProviderError):
    def __init__(self, seconds):
        super().__init__("provider account admission deferred", status_code=429,
                         retry_after=str(max(1, math.ceil(seconds))))


class ProviderQuotaCoordinator:
    def __init__(self, clock):
        self.clock = clock
        self._locks = {}
        self._until = {}

    def remaining(self, provider):
        family = account_family(provider)
        until = self._until.get(family)
        return max(0.0, (until - self.clock.now()).total_seconds()) if until else 0.0

    def observe(self, provider, status, retry_after=None):
        if status != 429:
            return
        delay = retry_after_seconds(retry_after, now=self.clock.now())
        if delay is None:
            delay = 60.0
        self.block_for(provider, delay)

    def block_for(self, provider, seconds):
        if not isinstance(seconds, (float, int)) or not math.isfinite(seconds):
            return
        family = account_family(provider)
        until = self.clock.now() + timedelta(seconds=max(1, min(604800, seconds)))
        existing = self._until.get(family)
        if existing is None or until > existing:
            self._until[family] = until

    @asynccontextmanager
    async def admission(self, provider, *, max_wait=2.0):
        family = account_family(provider)
        delay = self.remaining(provider)
        if delay:
            raise ProviderQuotaDeferred(delay)
        lock = self._locks.setdefault(family, asyncio.Lock())
        # asyncio.Lock is FIFO; bound the wait independently of network time.
        try:
            await asyncio.wait_for(lock.acquire(), timeout=max_wait)
        except TimeoutError:
            raise ProviderQuotaDeferred(1) from None
        try:
            delay = self.remaining(provider)
            if delay:
                raise ProviderQuotaDeferred(delay)
            try:
                yield
            except ProviderQuotaDeferred:
                raise
            except Exception as exc:
                self.observe(provider, getattr(exc, "status_code", None),
                             getattr(exc, "retry_after", None))
                raise
        finally:
            lock.release()

    def snapshot(self):
        return {family: {"cooldown_seconds": self.remaining(family),
                         "in_flight": lock.locked()}
                for family, lock in sorted(self._locks.items())}
