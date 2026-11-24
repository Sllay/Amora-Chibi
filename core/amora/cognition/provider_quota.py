"""Core-owned shared provider admission. No guessed upstream RPM/TPM limits.

One bounded FIFO asyncio lock per configured account family; rate-limit
observations are shared across tasks using the same credentials and model.
Groq documents per-model rate limits; other providers and missing model IDs
remain account-scoped unless their scope is established. Explicit account-wide
restrictions always take precedence. No request
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

    @staticmethod
    def _key(provider, model=None, *, account_wide=False):
        family = account_family(provider)
        # Groq: documented organization/project quotas per model.
        # https://console.groq.com/docs/rate-limits
        # https://console.groq.com/docs/projects
        return (family, model if family == "groq" and model and not account_wide else None)

    def remaining(self, provider, *, model=None):
        family, scoped_model = self._key(provider, model)
        now = self.clock.now()
        deadlines = [self._until.get((family, None))]
        if scoped_model is not None:
            deadlines.append(self._until.get((family, scoped_model)))
        return max((max(0.0, (until - now).total_seconds())
                    for until in deadlines if until is not None), default=0.0)

    def observe(self, provider, status, retry_after=None, *, model=None, account_wide=False):
        if status != 429:
            return
        delay = retry_after_seconds(retry_after, now=self.clock.now())
        if delay is None:
            delay = 60.0
        self.block_for(provider, delay, model=model, account_wide=account_wide)

    def block_for(self, provider, seconds, *, model=None, account_wide=False):
        if not isinstance(seconds, (float, int)) or not math.isfinite(seconds):
            return
        key = self._key(provider, model, account_wide=account_wide)
        until = self.clock.now() + timedelta(seconds=max(1, min(604800, seconds)))
        existing = self._until.get(key)
        if existing is None or until > existing:
            self._until[key] = until

    @asynccontextmanager
    async def admission(self, provider, *, model=None, max_wait=2.0):
        family = account_family(provider)
        delay = self.remaining(provider, model=model)
        if delay:
            raise ProviderQuotaDeferred(delay)
        lock = self._locks.setdefault(family, asyncio.Lock())
        # asyncio.Lock is FIFO; bound the wait independently of network time.
        try:
            await asyncio.wait_for(lock.acquire(), timeout=max_wait)
        except TimeoutError:
            raise ProviderQuotaDeferred(1) from None
        try:
            delay = self.remaining(provider, model=model)
            if delay:
                raise ProviderQuotaDeferred(delay)
            try:
                yield
            except ProviderQuotaDeferred:
                raise
            except Exception as exc:
                self.observe(provider, getattr(exc, "status_code", None),
                             getattr(exc, "retry_after", None), model=model,
                             account_wide=(getattr(exc, "diagnostics", {}).get("quota_scope") == "account"))
                raise
        finally:
            lock.release()

    def snapshot(self):
        families = set(self._locks) | {family for family, _ in self._until}
        return {
            family: {
                "cooldown_seconds": self.remaining(family),
                "in_flight": family in self._locks and self._locks[family].locked(),
                "model_cooldowns_seconds": {
                    model: self.remaining(family, model=model)
                    for name, model in self._until
                    if name == family and model is not None and self.remaining(family, model=model) > 0
                },
            }
            for family in sorted(families)
        }
